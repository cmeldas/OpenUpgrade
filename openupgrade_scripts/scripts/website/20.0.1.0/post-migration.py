# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def website_page_date_publish(env):
    """`website.page.date_publish` is removed. In 19.0 a published page with
    a future `date_publish` stayed hidden until that date; 20.0 schedules the
    publication with `publish_on` (cron `ir_cron_publish_scheduled_pages`).
    Past dates are kept as `published_date`.
    """
    cr = env.cr
    if not openupgrade.column_exists(cr, "website_page", "date_publish"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE website_page
        SET published_date = date_publish
        WHERE is_published
            AND date_publish IS NOT NULL
            AND date_publish <= (now() AT TIME ZONE 'UTC')
            AND published_date IS NULL
        """,
    )
    scheduled = openupgrade.logged_query(
        cr,
        """
        UPDATE website_page
        SET publish_on = date_publish, is_published = FALSE
        WHERE is_published
            AND date_publish > (now() AT TIME ZONE 'UTC')
            AND publish_on IS NULL
        """,
    )
    if scheduled:
        env["website.page"].invalidate_model(["publish_on", "is_published"])
        env["website.page"]._manage_next_scheduled_action()


def website_cookie_policy_page(env):
    """New `website.cookie_policy_id`: link the existing `/cookie-policy` page
    of each website showing the cookies bar, as 20.0 does when the bar is
    enabled (the link falls back to `/cookie-policy` anyway).
    """
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE website w
        SET cookie_policy_id = p.id
        FROM website_page p
        WHERE w.cookies_bar
            AND w.cookie_policy_id IS NULL
            AND p.website_id = w.id
            AND p.url->>'en_US' = '/cookie-policy'
            AND NOT EXISTS (
                SELECT 1 FROM website_page p2
                WHERE p2.website_id = w.id
                    AND p2.url->>'en_US' = '/cookie-policy'
                    AND p2.id < p.id
            )
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "website", "20.0.1.0/noupdate_changes.xml")
    website_page_date_publish(env)
    website_cookie_policy_page(env)
