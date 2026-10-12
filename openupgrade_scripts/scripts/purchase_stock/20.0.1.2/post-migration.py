# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def _date_promised(env):
    """New purchase.order.line.date_promised: 20.0 sets it to date_planned
    when the order is confirmed, then the order date_promised (stored
    compute, computed when its column was created) is the earliest one.
    """
    cr = env.cr
    openupgrade.logged_query(
        cr,
        """
        UPDATE purchase_order_line pol
        SET date_promised = pol.date_planned
        FROM purchase_order po
        WHERE po.id = pol.order_id
            AND po.state = 'purchase'
            AND pol.display_type IS NULL
            AND pol.date_promised IS NULL
            AND pol.date_planned IS NOT NULL
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE purchase_order po
        SET date_promised = lines.date_promised
        FROM (
            SELECT order_id, MIN(date_promised) AS date_promised
            FROM purchase_order_line
            WHERE display_type IS NULL AND date_promised IS NOT NULL
            GROUP BY order_id
        ) AS lines
        WHERE lines.order_id = po.id
            AND po.state IN ('purchase', 'cancel')
            AND po.date_promised IS DISTINCT FROM lines.date_promised
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _date_promised(env)
