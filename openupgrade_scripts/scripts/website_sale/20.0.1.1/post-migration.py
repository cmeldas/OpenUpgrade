# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def website_prevent_sale_for(cr):
    """`prevent_zero_price_sale` was renamed to `prevent_sale` in pre-migration;
    the new `prevent_sale_for` selection must say what is prevented.
    """
    openupgrade.logged_query(
        cr,
        """
        UPDATE website
        SET prevent_sale_for = 'zero_price'
        WHERE prevent_sale AND prevent_sale_for IS NULL
        """,
    )


def website_category_display_options(cr):
    """`show_category_title`, `show_category_description` and
    `align_category_content` moved from `product.public.category` to
    `website`. A website option is enabled when it was enabled on one of its
    categories (or of the categories shared by all websites); websites without
    categories keep the 20.0 defaults.
    """
    for field in (
        "show_category_title",
        "show_category_description",
        "align_category_content",
    ):
        legacy = openupgrade.get_legacy_name(field)
        if not openupgrade.column_exists(cr, "product_public_category", legacy):
            continue
        openupgrade.logged_query(
            cr,
            f"""
            UPDATE website w
            SET {field} = categ.value
            FROM (
                SELECT w2.id AS website_id,
                    bool_or(COALESCE(c.{legacy}, FALSE)) AS value
                FROM website w2
                JOIN product_public_category c
                    ON c.website_id = w2.id OR c.website_id IS NULL
                GROUP BY w2.id
            ) categ
            WHERE categ.website_id = w.id
                AND w.{field} IS DISTINCT FROM categ.value
            """,
        )


def product_document_shown_on_product_page(cr):
    """`product.document.shown_on_product_page` became the value
    `shown_on_product_page` of `attached_on_sale`. Documents also sent with
    quotations or orders keep that value (one choice only in 20.0).
    """
    legacy = openupgrade.get_legacy_name("shown_on_product_page")
    if not openupgrade.column_exists(cr, "product_document", legacy):
        return
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE product_document
        SET attached_on_sale = 'shown_on_product_page'
        WHERE {legacy} AND COALESCE(attached_on_sale, 'hidden') = 'hidden'
        """,
    )


def create_missing_checkout_steps(env):
    """20.0 adds the generic checkout steps `/shop/address` and
    `/shop/payment/transaction`. Website specific copies are only created
    when a website is created (`_create_checkout_steps`): copy the missing
    ones for the existing websites.
    """
    step_model = env["website.checkout.step"].sudo().with_context(active_test=False)
    generic_steps = step_model.search([("website_id", "=", False)])
    for website in env["website"].sudo().search([]):
        existing_hrefs = set(
            step_model.search([("website_id", "=", website.id)]).mapped("step_href")
        )
        for step in generic_steps.filtered(
            lambda step, hrefs=existing_hrefs: step.step_href not in hrefs
        ):
            # same rule as website._create_checkout_steps()
            is_published = True
            if step.step_href == "/shop/extra_info":
                is_published = (
                    website.with_context(website_id=website.id)
                    .viewref("website_sale.extra_info")
                    .active
                )
            step.copy({"website_id": website.id, "is_published": is_published})


def abandoned_cart_cron(env):
    """The abandoned cart cron (renamed in pre-migration) comes from a
    noupdate file and keeps its 19.0 code: point it to the 20.0 method, and
    set its active state from the websites settings as 20.0 does when the
    setting is saved (`_toggle_abandoned_cart_email_cron`).
    """
    cron = env.ref(
        "website_sale.ir_cron_send_abandoned_email", raise_if_not_found=False
    )
    if not cron:
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE ir_act_server
        SET code = 'model._cron_send_abandoned_cart_email()'
        WHERE id = %s AND code LIKE '%%model._send_abandoned_cart_email()%%'
        """,
        (cron.ir_actions_server_id.id,),
    )
    env["ir.actions.server"].invalidate_model(["code"])
    env["website"]._toggle_abandoned_cart_email_cron()


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr
    website_prevent_sale_for(cr)
    website_category_display_options(cr)
    product_document_shown_on_product_page(cr)
    create_missing_checkout_steps(env)
    abandoned_cart_cron(env)
