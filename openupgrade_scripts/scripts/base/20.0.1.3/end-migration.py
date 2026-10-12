# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import importlib.util
import logging
import os
import re

from lxml import etree
from openupgradelib import openupgrade

import odoo
from odoo.modules.module import get_manifest
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

# [] and [(1, '=', 1)]: no domain
_TRUE_DOMAIN_RE = re.compile(
    r"""^\s*(\[\s*\]|\[\s*\(\s*1\s*,\s*(["'])=\2\s*,\s*1\s*\)\s*\])\s*$"""
)
_FALSE_VALUES = ("0", "false", "False")
# XML-ID modules of records that do not come from module data files
_CUSTOM_MODULES = ("__export__", "__custom__", "studio_customization")


def _deactivate_from_snapshot(env):
    """ir.model.access and ir.rule records that were inactive in the previous
    version stay inactive as ir.access. One rule with several groups gives
    several ir.access records <xmlid>, <xmlid>_1, <xmlid>_2..."""
    snapshot = openupgrade.get_legacy_name("ir_model_data_access")
    if not openupgrade.table_exists(env.cr, snapshot):
        return
    openupgrade.logged_query(
        env.cr,
        f"""
        UPDATE ir_access ia
           SET active = FALSE
          FROM ir_model_data imd, {snapshot} s
         WHERE imd.model = 'ir.access' AND imd.res_id = ia.id
           AND s.active IS FALSE AND ia.active
           AND imd.module = s.module
           AND (imd.name = s.name OR imd.name ~ ('^' || s.name || '_[0-9]+$'))
        """,
    )


def _activate_from_snapshot(env):
    """Some ir.access records are created inactive by their module and
    enabled by the code when a feature is used, e.g.
    project.project_task_rule_portal_project_sharing and
    hr_timesheet.timesheet_line_rule_portal_user, which project.collaborator
    toggles. They replace an ir.model.access and an ir.rule with the same
    XML-ID that were toggled the same way in the previous version: keep them
    active when they were active. Records deactivated by other modules are
    deactivated again afterwards (_cross_module_noupdate_overrides)."""
    snapshot = openupgrade.get_legacy_name("ir_model_data_access")
    if not openupgrade.table_exists(env.cr, snapshot):
        return
    openupgrade.logged_query(
        env.cr,
        f"""
        UPDATE ir_access ia
           SET active = TRUE
          FROM ir_model_data imd, {snapshot} s
         WHERE imd.model = 'ir.access' AND imd.res_id = ia.id
           AND s.active IS TRUE AND NOT ia.active
           AND imd.module = s.module
           AND (imd.name = s.name OR imd.name ~ ('^' || s.name || '_[0-9]+$'))
           AND NOT EXISTS (
               SELECT 1 FROM {snapshot} s2
                WHERE s2.module = s.module AND s2.name = imd.name
                  AND s2.active IS FALSE)
        """,
    )


def _cross_module_noupdate_overrides(env):
    """Modules deactivate ir.access records of other modules in noupdate data
    (e.g. sale_timesheet deactivates account.account_analytic_line_rule_*).
    The ir.access records were created in this migration by their own module,
    so the noupdate overrides of modules updated later were skipped: apply
    the deactivations as a fresh installation would."""
    env.cr.execute("SELECT name FROM ir_module_module WHERE state = 'installed'")
    installed = {name for (name,) in env.cr.fetchall()}
    xmlids = set()
    for module in sorted(installed):
        manifest = get_manifest(module)
        if not manifest:
            continue
        for filename in manifest.get("data", []):
            if not filename.endswith(".xml"):
                continue
            try:
                with file_open(os.path.join(module, filename), "rb") as fp:
                    tree = etree.parse(fp)
            except Exception as e:  # pylint: disable=broad-except
                _logger.warning("Could not parse %s/%s: %s", module, filename, e)
                continue
            for record in tree.iter("record"):
                xmlid = record.get("id") or ""
                if record.get("model") != "ir.access" or "." not in xmlid:
                    continue
                if xmlid.split(".", 1)[0] == module:
                    continue
                for field in record.iter("field"):
                    if field.get("name") != "active":
                        continue
                    value = field.get("eval", field.text or "").strip()
                    if value in _FALSE_VALUES:
                        xmlids.add(xmlid)
    for xmlid in sorted(xmlids):
        record = env.ref(xmlid, raise_if_not_found=False)
        if record and record._name == "ir.access" and record.active:
            _logger.info("Deactivating %s (noupdate override of another module)", xmlid)
            record.active = False


def _load_ir_access_converter():
    """Load odoo/upgrade_code/19.4-00-ir-access.py (the name is no valid
    module name), like odoo/upgrade-util's import_code_upgrade()."""
    # odoo is a namespace package (no __file__)
    path = next(
        candidate
        for candidate in (
            os.path.join(base_path, "upgrade_code", "19.4-00-ir-access.py")
            for base_path in odoo.__path__
        )
        if os.path.isfile(candidate)
    )
    spec = importlib.util.spec_from_file_location("odoo_upgrade_code_ir_access", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _convert_custom_access(env):
    """ACLs and record rules without module (created in the UI, imported or
    from Studio) are not recreated by any module: convert them with the
    algorithm Odoo uses for the source code of modules. Module records are
    passed too (should_migrate=False), as custom rules combine with them."""
    cr = env.cr
    snapshot = openupgrade.get_legacy_name("ir_model_data_access")
    if not (
        openupgrade.table_exists(cr, "ir_model_access")
        and openupgrade.table_exists(cr, snapshot)
    ):
        return
    cr.execute(
        f"""
        SELECT 'ir.model.access', a.id, a.name, m.model, a.group_id,
               a.perm_create, a.perm_read, a.perm_write, a.perm_unlink,
               NULL, a.active, s.module
          FROM ir_model_access a
          JOIN ir_model m ON m.id = a.model_id
          LEFT JOIN {snapshot} s ON s.model = 'ir.model.access' AND s.res_id = a.id
        UNION ALL
        SELECT 'ir.rule', r.id, r.name, m.model, g.group_id,
               r.perm_create, r.perm_read, r.perm_write, r.perm_unlink,
               r.domain_force, r.active, s.module
          FROM ir_rule r
          JOIN ir_model m ON m.id = r.model_id
          LEFT JOIN rule_group_rel g ON g.rule_group_id = r.id
          LEFT JOIN {snapshot} s ON s.model = 'ir.rule' AND s.res_id = r.id
        """
    )
    rows = cr.fetchall()
    custom = [
        row for row in rows if row[11] is None or row[11] in _CUSTOM_MODULES
    ]
    if not custom:
        return
    converter = _load_ir_access_converter()
    group_everyone = env.ref("base.group_everyone")
    groups = env["res.groups"].with_context(active_test=False).search([])
    permrules = []
    for (
        type_,
        res_id,
        name,
        model,
        group_id,
        perm_c,
        perm_r,
        perm_w,
        perm_d,
        domain,
        active,
        module,
    ) in rows:
        if model not in env or not active:
            continue
        if group_id and group_id not in groups.ids:
            continue
        operations = {
            op
            for op, perm in zip("crud", (perm_c, perm_r, perm_w, perm_d), strict=True)
            if perm
        }
        if not operations:
            continue
        if type_ == "ir.model.access" and not group_id:
            # a global ACL grants access to everybody
            group_id = group_everyone.id
        domain = domain or ""
        if _TRUE_DOMAIN_RE.match(domain):
            domain = ""
        permrules.append(
            converter.Access(
                id=(type_, res_id, group_id),
                name=name or model,
                model=model,
                group=group_id or None,
                operations=operations,
                domain=domain,
                type=type_,
                should_migrate=module is None or module in _CUSTOM_MODULES,
            )
        )
    accesses = converter.convert(
        permrules, env["res.groups"]._get_group_definitions()
    )
    IrAccess = env["ir.access"]
    for access in accesses:
        vals = {
            "name": access.name,
            "model_id": env["ir.model"]._get_id(access.model),
            "group_id": access.group or False,
            "operation": "".join(op for op in "crud" if op in access.operations),
            "domain": access.domain or False,
        }
        try:
            with cr.savepoint():
                record = IrAccess.create(vals)
        except Exception as e:  # pylint: disable=broad-except
            _logger.warning("Custom access %s converted inactive: %s", vals, e)
            # the domain is only validated on active records
            with cr.savepoint():
                record = IrAccess.create(dict(vals, active=False))
        _logger.info(
            "Custom ACL/record rule %s converted to ir.access %s", access.id, record.id
        )
        if access.from_perm and not access.from_perm.should_migrate:
            # in v19 the custom rule restricted the operations granted by an
            # ACL of a module; that ACL is recreated by its module as an
            # unrestricted permission, which ir.access ORs with this one
            _logger.warning(
                "Custom record rule %s restricted the module ACL %s: the module "
                "access still grants %s without domain, review ir.access %s",
                access.id,
                access.from_perm.id,
                access.operations,
                record.id,
            )
    for row in custom:
        if not row[10]:
            _logger.warning(
                "Inactive custom %s %s (%s) was not converted to ir.access",
                row[0],
                row[1],
                row[2],
            )


@openupgrade.migrate()
def migrate(env, version):
    _deactivate_from_snapshot(env)
    _activate_from_snapshot(env)
    _cross_module_noupdate_overrides(env)
    _convert_custom_access(env)
    env["ir.access"].invalidate_model()
    openupgrade.disable_invalid_filters(env)
