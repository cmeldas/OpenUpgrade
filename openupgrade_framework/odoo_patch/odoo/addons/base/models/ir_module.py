# Copyright Odoo Community Association (OCA)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import importlib
import logging

from openupgradelib import openupgrade

from odoo import api

from odoo.addons.base.models.ir_module import IrModuleModule

_logger = logging.getLogger(__name__)

INSTALLED_STATES = ("to upgrade", "to install", "installed")


def _get_apriori_attribute(name, default):
    """Return an attribute of openupgrade_scripts' apriori module, if available"""
    try:
        apriori = importlib.import_module("odoo.addons.openupgrade_scripts.apriori")
    except ImportError:
        return default
    return getattr(apriori, name, default)


def _get_previously_installed_modules(env):
    """Return the names of the modules that were installed before the migration.

    The framework takes a snapshot when it loads the first module graph, before
    any migration script ran (see the load_module_graph patch). The base
    pre-migration script may keep a copy of ir_module_module in a legacy table
    as well, which survives a restart of the migration.
    """
    names = set(getattr(env.registry, "_openupgrade_installed_modules", ()))
    legacy_table = openupgrade.get_legacy_name("ir_module_module")
    if openupgrade.table_exists(env.cr, legacy_table):
        env.cr.execute(
            f"SELECT name FROM {legacy_table} "  # pylint: disable=sql-injection
            "WHERE state IN ('installed', 'to upgrade', 'to remove')"
        )
        names.update(name for (name,) in env.cr.fetchall())
    return names


def _get_split_modules(env, split_modules, previously_installed):
    """Return the modules that must be installed because they took over models,
    data or features from modules that were installed before the migration.

    :param split_modules: mapping of module names of the previous version to
        the name (or the list of names) of the modules that part of them moved
        to (``apriori.split_modules``). These modules are installed when the
        module of the previous version was installed.
    :param previously_installed: names of the modules that were installed
        before the migration started. A module that a migration script merged
        into another one (and thus is no longer in the database) counts as
        installed when it is in this set.
    """
    Module = env["ir.module.module"]
    installed = previously_installed | set(
        Module.search(
            [("name", "in", list(split_modules)), ("state", "in", INSTALLED_STATES)]
        ).mapped("name")
    )
    to_install = Module.browse()
    for origin, names in split_modules.items():
        if origin not in installed:
            continue
        if isinstance(names, str):
            names = [names]
        for name in names:
            module = Module.search([("name", "=", name)])
            if not module:
                _logger.warning(
                    "Module %s split from %s is not available, cannot install it",
                    name,
                    origin,
                )
            elif module.state == "uninstalled":
                _logger.info("Installing module %s split from %s", name, origin)
                to_install |= module
            elif module.state not in INSTALLED_STATES:
                _logger.warning(
                    "Module %s split from %s is %s, cannot install it",
                    name,
                    origin,
                    module.state,
                )
    return to_install


@api.model
def update_list(self):
    """
    Mark auto_install modules as to install if all their dependencies are some kind of
    installed.
    Ignore localization modules that are set to auto_install, country specific
    modules for countries no company is in, and the modules that
    openupgrade_scripts' apriori lists in `no_auto_install_modules`.

    Also mark as to install the modules that took over models or data from
    installed modules, as listed in `split_modules` of openupgrade_scripts'
    apriori (these new modules are often not auto_install).
    """
    result = IrModuleModule.update_list._original_method(self)
    no_auto_install = set(_get_apriori_attribute("no_auto_install_modules", ()))
    company_countries = self.env["res.company"].search([]).country_id
    new_auto_install_modules = self.browse([])
    for module in self.env["ir.module.module"].search(
        [
            ("auto_install", "=", True),
            ("state", "=", "uninstalled"),
            ("name", "not like", ("l10n_%")),
        ]
    ):
        if module.name in no_auto_install:
            continue
        if module.country_ids and not module.country_ids & company_countries:
            continue
        if all(
            state in INSTALLED_STATES
            for state in module.dependencies_id.mapped("state")
        ):
            new_auto_install_modules |= module
    split_modules = _get_apriori_attribute("split_modules", {})
    if split_modules:
        new_auto_install_modules |= _get_split_modules(
            self.env,
            split_modules,
            _get_previously_installed_modules(self.env),
        )
    if new_auto_install_modules:
        new_auto_install_modules.button_install()
    return result


def check_external_dependencies(self, module_name, newstate="to install"):
    try:
        IrModuleModule.check_external_dependencies._original_method(
            self, module_name, newstate=newstate
        )
    except AttributeError:  # pylint: disable=except-pass
        # this happens when a module is installed that doesn't exist in the new version
        pass


update_list._original_method = IrModuleModule.update_list
IrModuleModule.update_list = update_list
check_external_dependencies._original_method = (
    IrModuleModule.check_external_dependencies
)
IrModuleModule.check_external_dependencies = check_external_dependencies
