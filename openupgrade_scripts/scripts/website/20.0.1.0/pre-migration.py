# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

SOCIAL_NETWORKS = (
    "discord",
    "facebook",
    "github",
    "instagram",
    "linkedin",
    "tiktok",
    "twitter",
    "youtube",
)

# Template menu entries pointing to a controller, which the first 19.0 script
# (before OCA/OpenUpgrade#5991) overwrote with '#'.
TEMPLATE_CONTROLLER_MENUS = (
    ("website_sale", "menu_shop", "/shop"),
    ("website_blog", "menu_blog", "/blog"),
    ("website_event", "menu_events", "/event"),
    ("website_forum", "menu_website_forums", "/forum"),
    ("website_slides", "website_menu_slides", "/slides"),
    ("website_hr_recruitment", "website_menu_jobs", "/jobs"),
)


def repair_template_menu_urls(cr):
    """Restore the URL of the template menu entries pointing to a controller.

    The first version of the 19.0 pre-migration script set the URL of every
    menu entry without a page to '#'. The template entries below
    `website.main_menu` are noupdate, so the module update did not restore
    them. Only rows that are still broken are touched ('#', no page, no
    children, no mega menu).
    """
    for module, name, url in TEMPLATE_CONTROLLER_MENUS:
        openupgrade.logged_query(
            cr,
            """
            UPDATE website_menu m
            SET url = %(url)s
            FROM ir_model_data d
            WHERE d.model = 'website.menu'
                AND d.module = %(module)s
                AND d.name = %(name)s
                AND m.id = d.res_id
                AND m.url = '#'
                AND m.page_id IS NULL
                AND m.controller_page_id IS NULL
                AND m.mega_menu_content IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM website_menu c WHERE c.parent_id = m.id
                )
            """,
            {"module": module, "name": name, "url": url},
            skip_no_result=True,
        )


def reset_template_menu_website(cr):
    """The template menu tree (`website.main_menu`) must not belong to a
    website: it is the tree copied for new websites. Saving the menu editor
    could set `website_id` on it; reset it when the website has its own root
    menu, so the website keeps showing that one.
    """
    openupgrade.logged_query(
        cr,
        """
        UPDATE website_menu m
        SET website_id = NULL
        FROM ir_model_data d
        WHERE d.model = 'website.menu'
            AND d.module = 'website'
            AND d.name = 'main_menu'
            AND (m.id = d.res_id OR m.parent_path LIKE d.res_id || '/%%')
            AND m.website_id IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM website_menu r
                WHERE r.parent_id IS NULL
                    AND r.website_id = m.website_id
                    AND r.id != d.res_id
            )
        """,
    )


def website_menu_url_to_manual_url(cr):
    """`website.menu.url` is a non stored computed field in 20.0, the URL
    typed by the user is stored in `manual_url`. The computed URL takes the
    page URL when there is a page, and '#' for empty values, menus with
    children and mega menus, so only the user URLs are kept.
    """
    if not openupgrade.column_exists(
        cr, "website_menu", "url"
    ) or openupgrade.column_exists(cr, "website_menu", "manual_url"):
        return
    openupgrade.rename_columns(cr, {"website_menu": [("url", "manual_url")]})
    openupgrade.logged_query(
        cr, "ALTER TABLE website_menu ALTER COLUMN manual_url DROP NOT NULL"
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE website_menu
        SET manual_url = NULL
        WHERE page_id IS NOT NULL OR manual_url IN ('', '#')
        """,
    )


def website_social_to_company(cr):
    """`website.social_*` are removed, the social links of the company
    (`social_media`) remain. Copy the website values to the company where
    the company value is empty. The website columns are kept, end-migration
    uses them to replace the removed `/website/social/<network>` route.
    """
    for network in SOCIAL_NETWORKS:
        column = "social_%s" % network
        if not openupgrade.column_exists(
            cr, "website", column
        ) or not openupgrade.column_exists(cr, "res_company", column):
            continue
        openupgrade.logged_query(
            cr,
            f"""
            UPDATE res_company c
            SET {column} = w.{column}
            FROM (
                SELECT DISTINCT ON (company_id) company_id, {column}
                FROM website
                WHERE COALESCE({column}, '') != ''
                ORDER BY company_id, sequence, id
            ) w
            WHERE w.company_id = c.id AND COALESCE(c.{column}, '') = ''
            """,
            skip_no_result=True,
        )


def cow_templates_mark_if_equal_ignoring_languages(cr):
    """`cow_templates_mark_if_equal_to_upstream` compares the whole `arch_db`
    jsonb. A COW copy saved in a non English interface has the same arch as
    the generic view, but also stores it under that language key while the
    generic view only has `en_US`. Such copies are unmodified too: mark them
    when every language value equals the generic value for that language (or
    its `en_US` value when the generic has no such language).
    """
    column = openupgrade.get_legacy_name("cow_equal_to_upstream")
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE ir_ui_view specific
        SET {column} = TRUE
        FROM ir_ui_view generic
        WHERE specific.website_id IS NOT NULL
            AND generic.website_id IS NULL
            AND specific.key = generic.key
            AND specific.type = 'qweb'
            AND generic.type = 'qweb'
            AND specific.{column} IS NOT TRUE
            AND specific.arch_db->>'en_US' = generic.arch_db->>'en_US'
            AND NOT EXISTS (
                SELECT 1 FROM jsonb_each_text(specific.arch_db) lang
                WHERE lang.value IS DISTINCT FROM COALESCE(
                    generic.arch_db->>lang.key, generic.arch_db->>'en_US'
                )
            )
        """,
    )


def view_visibility_public(cr):
    """`ir.ui.view.visibility` is required in 20.0 with the new value
    'public', which replaces the empty value used for public views in 19.0.
    """
    if openupgrade.column_exists(cr, "ir_ui_view", "visibility"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE ir_ui_view SET visibility = 'public'
            WHERE visibility IS NULL OR visibility = ''
            """,
        )


# Snippet templates defined in the 19.0 XML files (updatable) that 20.0
# generates from the manifest (`_generate_primary_snippet_templates`, noupdate
# XML-IDs). The generator does not touch existing views and does not mark
# their XML-IDs as loaded, so `_process_end` would delete them with all their
# (generated, noupdate) children, among them templates the 20.0 "New page"
# dialog uses. Their 19.0 arch is the same empty primary extension.
_GENERATED_SNIPPET_TEMPLATES = [
    "new_page_template_s_call_to_action",
    "new_page_template_s_features",
    "new_page_template_s_images_wall",
    "new_page_template_s_parallax",
    "new_page_template_services_s_parallax",
]


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr
    openupgrade.set_xml_ids_noupdate_value(
        env, "website", _GENERATED_SNIPPET_TEMPLATES, True
    )
    view_visibility_public(cr)
    repair_template_menu_urls(cr)
    reset_template_menu_website(cr)
    website_menu_url_to_manual_url(cr)
    website_social_to_company(cr)
    openupgrade.cow_templates_mark_if_equal_to_upstream(cr)
    cow_templates_mark_if_equal_ignoring_languages(cr)
