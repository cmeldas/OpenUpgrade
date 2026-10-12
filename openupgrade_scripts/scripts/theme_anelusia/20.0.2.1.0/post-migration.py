# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

THEME_TEMPLATE_MODELS = (
    "theme.ir.ui.view",
    "theme.ir.asset",
    "theme.ir.attachment",
    "theme.website.page",
    "theme.website.menu",
)


def delete_obsolete_theme_templates(env, module):
    """Delete the theme templates the 20.0 theme does not define anymore.

    Right after this script, Odoo marks the module installed and
    `ir.module.module.write()` reloads the theme on its websites
    (`_theme_load`), which ends with `_theme_cleanup`: it deletes the
    website copies (views, assets) whose template is gone. But the obsolete
    templates themselves are only deleted later by `_process_end`, so without
    this their copies would stay on the websites with their 19.0 content
    (e.g. `theme_anelusia.s_card_offset`, whose xpath does not apply on the
    20.0 snippet anymore and breaks the snippets panel of the editor, or
    assets pointing to removed `old_snippets/*.scss` files).
    """
    cr = env.cr
    # only defined while the registry is loading
    loaded = getattr(env.registry, "loaded_xmlids", None)
    if loaded is None:
        return
    cr.execute(
        """
        SELECT module || '.' || name FROM ir_model_data
        WHERE module = %s AND model IN %s AND NOT COALESCE(noupdate, FALSE)
        ORDER BY id DESC
        """,
        (module, THEME_TEMPLATE_MODELS),
    )
    obsolete = [row[0] for row in cr.fetchall() if row[0] not in loaded]
    if obsolete:
        _logger.info("Deleting %s obsolete %s templates", len(obsolete), module)
        openupgrade.delete_records_safely_by_xml_id(env, obsolete)


@openupgrade.migrate()
def migrate(env, version):
    delete_obsolete_theme_templates(env, "theme_anelusia")
