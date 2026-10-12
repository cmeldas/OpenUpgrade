# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_fields = [
    ("pos.order.line", "pos_order_line", "coupon_id", "card_id"),
]


def _topup_lines_card(env):
    """v20 also sets card_id on the line that sold / topped up a gift card or
    an eWallet, and a refund of such a line finds the card to debit through
    refunded_orderline_id.card_id. v19 only linked reward lines (coupon_id).
    Link the top-up lines of past orders: the card is known from the history
    line the order created, the line is the non-reward line of the order whose
    product triggers the program (paired in id order when an order sold
    several cards of the same program)."""
    openupgrade.logged_query(
        env.cr,
        """
        WITH topups AS (
            SELECT h.order_id, h.card_id, card.program_id,
                   row_number() OVER (
                       PARTITION BY h.order_id, card.program_id ORDER BY h.id
                   ) AS rn
              FROM loyalty_history h
              JOIN loyalty_card card ON card.id = h.card_id
              JOIN loyalty_program program ON program.id = card.program_id
             WHERE h.order_model = 'pos.order'
               AND h.issued > 0
               AND program.program_type IN ('gift_card', 'ewallet')
        ), topup_lines AS (
            SELECT line.id AS line_id, line.order_id, trig.program_id,
                   row_number() OVER (
                       PARTITION BY line.order_id, trig.program_id
                       ORDER BY line.id
                   ) AS rn
              FROM pos_order_line line
              JOIN (
                  SELECT DISTINCT rule.program_id, rel.product_product_id
                    FROM loyalty_rule rule
                    JOIN loyalty_rule_product_product_rel rel
                      ON rel.loyalty_rule_id = rule.id
              ) trig ON trig.product_product_id = line.product_id
             WHERE NOT COALESCE(line.is_reward_line, FALSE)
               AND line.card_id IS NULL
               AND line.qty > 0
        )
        UPDATE pos_order_line pol
           SET card_id = topups.card_id
          FROM topups
          JOIN topup_lines
            ON topup_lines.order_id = topups.order_id
           AND topup_lines.program_id = topups.program_id
           AND topup_lines.rn = topups.rn
         WHERE pol.id = topup_lines.line_id
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.rename_fields(env, _renamed_fields)
    _topup_lines_card(env)
