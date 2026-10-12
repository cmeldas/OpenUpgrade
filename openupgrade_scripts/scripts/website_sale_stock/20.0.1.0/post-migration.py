# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    """The back in stock subscriptions (`product.product`
    `stock_notification_partner_ids`, table
    `stock_notification_product_partner_rel`) became the model
    `product.stock.notification` of website_sale (table
    `product_stock_notification_rel`), with a required website.
    19.0 did not store the website: use the partner's website, or else the
    first website of the partner's company (or the first website).
    """
    cr = env.cr
    if not openupgrade.table_exists(
        cr, "stock_notification_product_partner_rel"
    ) or not openupgrade.table_exists(cr, "product_stock_notification_rel"):
        return
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO product_stock_notification_rel (
            product_id, partner_id, website_id,
            create_uid, create_date, write_uid, write_date
        )
        SELECT rel.product_product_id, rel.res_partner_id,
            COALESCE(
                partner.website_id,
                (
                    SELECT w.id FROM website w
                    WHERE w.company_id = partner.company_id
                    ORDER BY w.sequence, w.id LIMIT 1
                ),
                (SELECT w.id FROM website w ORDER BY w.sequence, w.id LIMIT 1)
            ),
            1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
        FROM stock_notification_product_partner_rel rel
        JOIN res_partner partner ON partner.id = rel.res_partner_id
        JOIN product_product product ON product.id = rel.product_product_id
        WHERE NOT EXISTS (
            SELECT 1 FROM product_stock_notification_rel psn
            WHERE psn.product_id = rel.product_product_id
                AND psn.partner_id = rel.res_partner_id
        )
        """,
        skip_no_result=True,
    )
