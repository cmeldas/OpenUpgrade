# Copyright Odoo Community Association (OCA)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import odoo.modules.loading


def load_module_graph(env, graph, *args, **kwargs):
    """
    Force run pre-migration scripts for modules being installed

    Also remember which modules were installed before any migration script
    ran (the first graph to load is base's, before its pre-migration script).
    Scripts may merge or rename modules, but the framework still needs to know
    the original module set, for instance to install split modules
    (see the update_list patch).
    """
    registry = env.registry
    if not hasattr(registry, "_openupgrade_installed_modules"):
        env.cr.execute(
            "SELECT name FROM ir_module_module WHERE state IN %s",
            (("installed", "to upgrade", "to remove"),),
        )
        registry._openupgrade_installed_modules = {
            name for (name,) in env.cr.fetchall()
        }
    registry._force_upgrade_scripts.update({package.name for package in graph})
    return odoo.modules.loading.load_module_graph._original_method(
        env, graph, *args, **kwargs
    )


load_module_graph._original_method = odoo.modules.loading.load_module_graph
odoo.modules.loading.load_module_graph = load_module_graph
