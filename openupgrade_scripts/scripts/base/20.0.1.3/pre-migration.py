# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2 import sql

# pylint: disable=odoo-addons-relative-import
from odoo.addons.openupgrade_scripts.apriori import merged_modules, renamed_modules

_renamed_xmlids = [
    # India: Odisha changed its xmlid, the code 'OD' stays the same
    ("base.state_in_or", "base.state_in_od"),
    # the VIES cron of base_vat lives in l10n_eu_account_vies, which gets
    # installed through apriori.split_modules
    (
        "base_vat.vies_iap_check_update",
        "l10n_eu_account_vies.vies_iap_check_update",
    ),
    (
        "base_vat.vies_iap_check_update_ir_actions_server",
        "l10n_eu_account_vies.vies_iap_check_update_ir_actions_server",
    ),
]

_renamed_fields = [
    ("res.partner.bank", "res_partner_bank", "acc_number", "account_number"),
    ("res.partner.bank", "res_partner_bank", "acc_holder_name", "holder_name"),
    (
        "res.partner.bank",
        "res_partner_bank",
        "sanitized_acc_number",
        "sanitized_account_number",
    ),
]

# models that do not exist anymore in v20 and whose XML-IDs collide with (or
# would be orphaned next to) the records that replace them
_removed_models = ["ir.model.access", "ir.rule", "res.bank"]


def _snapshot_module_states(env):
    """Keep the module states of the previous version, before any renaming or
    merging. Used by the framework to install split modules
    (apriori.split_modules) and by later scripts."""
    openupgrade.logged_query(
        env.cr,
        f"""
        CREATE TABLE {openupgrade.get_legacy_name("ir_module_module")} AS
        (SELECT name, state, latest_version FROM ir_module_module)
        """,
    )


def _move_website_model_to_base(env):
    """The model `website` was moved from website to base, which creates
    base.default_website (noupdate): keep the existing website instead of
    creating a second one."""
    if not openupgrade.table_exists(env.cr, "website"):
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE ir_model_data
           SET module = 'base'
         WHERE model = 'website' AND module = 'website'
           AND name NOT IN (
               SELECT name FROM ir_model_data
                WHERE module = 'base' AND model = 'website')
        """,
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE ir_model_constraint c
           SET module = (SELECT id FROM ir_module_module WHERE name = 'base')
          FROM ir_module_module m
         WHERE c.module = m.id AND m.name = 'website'
           AND c.name = 'website_domain_unique'
        """,
    )
    openupgrade.rename_xmlids(
        env.cr,
        [
            (
                "website.constraint_website_domain_unique",
                "base.constraint_website_domain_unique",
            )
        ],
    )


def _ir_access_xmlids(env):
    """ir.model.access and ir.rule are replaced by ir.access, whose records
    reuse the same XML-IDs (module.name). Odoo refuses to load a record
    whose XML-ID points to a record of another model, so the old XML-IDs
    have to go before the data of the modules is loaded. The ACL and rule
    tables are kept (Odoo never drops tables of removed models); the
    snapshot of the XML-IDs is used in end-migration to carry over the
    deactivated records and to convert records that do not come from a
    module."""
    cr = env.cr
    snapshot = openupgrade.get_legacy_name("ir_model_data_access")
    openupgrade.logged_query(
        cr,
        f"""
        CREATE TABLE {snapshot} AS
        SELECT d.module, d.name, d.model, d.res_id, d.noupdate,
               COALESCE(a.active, r.active) AS active
          FROM ir_model_data d
          LEFT JOIN ir_model_access a
            ON d.model = 'ir.model.access' AND a.id = d.res_id
          LEFT JOIN ir_rule r ON d.model = 'ir.rule' AND r.id = d.res_id
         WHERE d.model IN ('ir.model.access', 'ir.rule')
        """,
    )
    openupgrade.delete_xmlids_of_removed_models(cr, _removed_models)


def _ir_model_fields_index(env):
    """index: boolean -> selection. The automatic cast would give 'true' and
    'false', which are no valid selection values."""
    if openupgrade.column_exists(env.cr, "ir_model_fields", "index"):
        env.cr.execute(
            """
            SELECT data_type FROM information_schema.columns
             WHERE table_name = 'ir_model_fields' AND column_name = 'index'
            """
        )
        if env.cr.fetchone()[0] == "boolean":
            openupgrade.logged_query(
                env.cr,
                """
                ALTER TABLE ir_model_fields
                    ALTER COLUMN index DROP DEFAULT,
                    ALTER COLUMN index TYPE varchar
                        USING CASE WHEN index THEN 'btree' END
                """,
            )


def _ir_act_client_params_store(env):
    """params_store: Binary (bytea holding the repr of a dict) -> Text. The
    automatic cast would store the hexadecimal representation."""
    env.cr.execute(
        """
        SELECT data_type FROM information_schema.columns
         WHERE table_name = 'ir_act_client' AND column_name = 'params_store'
        """
    )
    row = env.cr.fetchone()
    if row and row[0] == "bytea":
        openupgrade.logged_query(
            env.cr,
            """
            ALTER TABLE ir_act_client
                ALTER COLUMN params_store TYPE text
                    USING convert_from(params_store, 'UTF8')
            """,
        )


def _res_device_log(env):
    """user_agent is a new required field (browser and platform are gone),
    user_id is now a required integer (the user is kept after deletion)."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "res_device_log", "user_agent"):
        openupgrade.logged_query(
            cr, "ALTER TABLE res_device_log ADD COLUMN user_agent varchar"
        )
        if openupgrade.column_exists(cr, "res_device_log", "browser"):
            openupgrade.logged_query(
                cr,
                """
                UPDATE res_device_log
                   SET user_agent = NULLIF(concat_ws(' ', platform, browser), '')
                """,
            )
        openupgrade.logged_query(
            cr,
            "UPDATE res_device_log SET user_agent = 'Unknown' WHERE user_agent IS NULL",
        )
    # logs of sessions without user (public visitors) cannot be kept
    openupgrade.logged_query(cr, "DELETE FROM res_device_log WHERE user_id IS NULL")
    # user_id is an Integer now, on purpose without foreign key (the logs of
    # deleted users are kept). Odoo does not drop the foreign key of the
    # former many2one; its ON DELETE SET NULL would make every deletion of a
    # user with device logs fail on the new NOT NULL constraint.
    cr.execute(
        """
        SELECT c.conname
          FROM pg_constraint c
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
         WHERE c.conrelid = 'res_device_log'::regclass AND c.contype = 'f'
           AND a.attname = 'user_id'
        """
    )
    for (conname,) in cr.fetchall():
        openupgrade.logged_query(
            cr,
            sql.SQL("ALTER TABLE res_device_log DROP CONSTRAINT {}").format(
                sql.Identifier(conname)
            ),
        )
        openupgrade.logged_query(
            cr,
            "DELETE FROM ir_model_constraint WHERE name = %s AND type = 'f'",
            (conname,),
        )


def _ir_act_server_update_property(env):
    """ir.actions.server.update_property is a new stored computed field. When
    the ORM creates its column while loading base, it recomputes every server
    action, and for "update record" actions on models of modules that are not
    loaded yet the compute fails (KeyError on the model). The value only
    matters for paths through properties fields: pre-create it empty.
    """
    openupgrade.logged_query(
        env.cr,
        "ALTER TABLE ir_act_server ADD COLUMN IF NOT EXISTS update_property varchar",
    )


@openupgrade.migrate()
def migrate(env, version):
    _snapshot_module_states(env)
    # before the merge of base_vat, which would move its XML-IDs to base
    openupgrade.rename_xmlids(env.cr, _renamed_xmlids)
    openupgrade.update_module_names(
        env.cr, renamed_modules.items(), environment_namespec=True
    )
    openupgrade.update_module_names(
        env.cr, merged_modules.items(), merge_modules=True, environment_namespec=True
    )
    openupgrade.clean_transient_models(env.cr)
    _move_website_model_to_base(env)
    _ir_access_xmlids(env)
    _ir_model_fields_index(env)
    _ir_act_client_params_store(env)
    _ir_act_server_update_property(env)
    openupgrade.rename_fields(env, _renamed_fields)
    _res_device_log(env)
