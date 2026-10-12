from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import sql
from odoo.tools.misc import mute_logger

from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG

from ..odoo_patch.odoo.addons.base.models.ir_module import (
    _get_previously_installed_modules,
    _get_split_modules,
)

PATCH_LOGGER = "odoo.addons.openupgrade_framework.odoo_patch.odoo"
MODELS_LOGGER = PATCH_LOGGER + ".orm.models"
IR_MODULE_LOGGER = PATCH_LOGGER + ".addons.base.models.ir_module"


class TestOpenupgradeFramework(TransactionCase):
    def test_01_delete_undeletable_record(self):
        """
        Test that Odoo doesn't crash when deleting records that can't be deleted
        during module upgrade
        """
        deletable = self.env["res.partner"].create({"name": "Obsolete partner"})
        partner = self.env.ref("base.partner_admin")
        with mute_logger("odoo.sql_db"), self.assertLogs(MODELS_LOGGER):
            self.assertTrue(
                deletable.with_context(**{MODULE_UNINSTALL_FLAG: True}).unlink()
            )
            self.assertFalse(
                partner.with_context(**{MODULE_UNINSTALL_FLAG: True}).unlink()
            )
        # the failed deletion is rolled back, the successful one is not, and the
        # ORM is still usable
        self.assertTrue(partner.exists())
        self.assertTrue(partner.name)
        self.assertFalse(deletable.exists())
        self.assertEqual(self.env.ref("base.user_admin").partner_id, partner)

    def test_02_invalid_view(self):
        """
        Test that we patch away fatal view errors and log the problem
        """
        with self.assertLogs(PATCH_LOGGER + ".addons.base.models.ir_ui_view"):
            self.env["ir.ui.view"].create(
                {
                    "model": "res.partner",
                    "arch": '<form><field name="nonexisting_field" /></form>',
                }
            )

    def test_03_ondelete_check_does_not_prevent_forced_deletion(self):
        """
        As of Odoo 20, @api.ondelete(at_uninstall=False) checks run on forced
        deletions outside of module uninstallation. Their failure must not
        prevent the deletion of obsolete records, like up to Odoo 19.
        """
        param = self.env["ir.config_parameter"].search([("key", "=", "database.uuid")])
        self.assertTrue(param)
        with self.assertLogs(MODELS_LOGGER, level="WARNING") as logs:
            self.assertTrue(param.with_context(force_delete=True).unlink())
        self.assertIn("unlink_default_parameters", logs.output[0])
        self.assertFalse(param.exists())
        # the class attribute is restored, normal deletions still check
        param = self.env["ir.config_parameter"].search(
            [("key", "=", "database.create_date")]
        )
        with self.assertRaises(ValidationError):
            param.unlink()

    def test_04_keep_attachments_of_removed_models(self):
        """
        Odoo 20 deletes attachments (and their files) of deleted models. Don't do
        that for models removed during a migration.
        """
        model = self.env["ir.model"].create(
            {"name": "OpenUpgrade test model", "model": "x_ou_test", "state": "manual"}
        )
        attachment = self.env["ir.attachment"].create(
            {"name": "ou.txt", "res_model": "x_ou_test", "res_id": 1, "raw": b"OU"}
        )
        with self.assertLogs(MODELS_LOGGER, level="INFO") as logs:
            model.with_context(force_delete=True).unlink()
        self.assertTrue(
            any("_unlink_related_attachments" in line for line in logs.output)
        )
        self.assertFalse(model.exists())
        self.env.cr.execute(
            "SELECT count(*) FROM ir_attachment WHERE id = %s", (attachment.id,)
        )
        self.assertEqual(self.env.cr.fetchone()[0], 1)

    def test_05_process_end_unlink_record(self):
        """
        _process_end() must not crash on a record that cannot be deleted
        """
        partner = self.env.ref("base.partner_admin")
        with mute_logger("odoo.sql_db", MODELS_LOGGER):
            self.env["ir.model.data"].with_context(
                force_delete=True
            )._process_end_unlink_record(partner.with_context(force_delete=True))
        self.assertTrue(partner.exists())

    def test_06_convert_column_fallback(self):
        """
        A column type conversion that fails keeps the data in a legacy column
        """
        cr = self.env.cr
        cr.execute(
            "CREATE TABLE ou_test_convert (id serial PRIMARY KEY, color varchar NOT NULL);"
            "INSERT INTO ou_test_convert (color) VALUES ('#3C3C3C'), ('2')"
        )
        with self.assertLogs(
            "odoo.addons.openupgrade_framework.odoo_patch.odoo.tools.sql",
            level="ERROR",
        ):
            sql.convert_column(cr, "ou_test_convert", "color", "int4")
        self.assertEqual(self._column_type("ou_test_convert", "color"), "int4")
        cr.execute(
            "SELECT openupgrade_legacy_20_0_color, color FROM ou_test_convert "
            "ORDER BY id"
        )
        self.assertEqual(cr.fetchall(), [("#3C3C3C", None), ("2", None)])
        # the legacy column does not prevent new rows (it was NOT NULL)
        cr.execute("INSERT INTO ou_test_convert (color) VALUES (3)")
        # a conversion that works is not affected
        sql.convert_column(
            cr, "ou_test_convert", "openupgrade_legacy_20_0_color", "text"
        )
        self.assertEqual(
            self._column_type("ou_test_convert", "openupgrade_legacy_20_0_color"),
            "text",
        )

    def _column_type(self, table, column):
        self.env.cr.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        return self.env.cr.fetchone()[0]

    def test_07_split_modules(self):
        """
        Modules split from installed modules are marked to be installed
        """
        module = self.env["ir.module.module"].search(
            [("state", "=", "uninstalled")], limit=1
        )
        self.assertTrue(module)
        self.assertEqual(
            _get_split_modules(self.env, {"base": [module.name]}, set()), module
        )
        self.assertEqual(
            _get_split_modules(self.env, {"base": module.name}, set()), module
        )
        # origin merged away by a migration script, but installed before
        self.assertEqual(
            _get_split_modules(self.env, {"ou_merged": [module.name]}, {"ou_merged"}),
            module,
        )
        self.assertFalse(
            _get_split_modules(self.env, {"ou_merged": [module.name]}, set())
        )
        # already installed: nothing to do
        self.assertFalse(_get_split_modules(self.env, {"base": ["web"]}, set()))
        with self.assertLogs(IR_MODULE_LOGGER, level="WARNING"):
            self.assertFalse(
                _get_split_modules(self.env, {"base": ["ou_not_available"]}, set())
            )

    def test_08_previously_installed_modules(self):
        """
        The modules installed before the migration come from the snapshot of
        the framework and from the legacy copy of the base pre-migration
        """
        cr = self.env.cr
        self.assertFalse(
            {"ou_merged", "ou_removed"} & _get_previously_installed_modules(self.env)
        )
        cr.execute(
            "CREATE TABLE IF NOT EXISTS openupgrade_legacy_20_0_ir_module_module "
            "(name varchar, state varchar, latest_version varchar)"
        )
        cr.execute(
            "INSERT INTO openupgrade_legacy_20_0_ir_module_module (name, state) "
            "VALUES ('ou_merged', 'installed'), ('ou_removed', 'uninstalled')"
        )
        names = _get_previously_installed_modules(self.env)
        self.assertIn("ou_merged", names)
        self.assertNotIn("ou_removed", names)
