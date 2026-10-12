# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


def _loyalty_history_cleanup(env):
    """v20 sums loyalty.history.issued / used per card (read_group): a card
    whose lines have only NULL in one of the columns gets None and the
    computation fails. Earlier versions left NULL in the column not set by the
    wizard/import (e.g. used of a manual top-up). Rows that award and consume
    at once or carry negative amounts would violate the new SQL constraints
    issued_or_used / issued_and_used_positive: split / flip them."""
    cr = env.cr
    openupgrade.logged_query(
        cr, "UPDATE loyalty_history SET issued = 0 WHERE issued IS NULL"
    )
    openupgrade.logged_query(cr, "UPDATE loyalty_history SET used = 0 WHERE used IS NULL")
    # negative values: an awarded -x is a consumption of x and vice versa
    openupgrade.logged_query(
        cr,
        """
        UPDATE loyalty_history
           SET issued = GREATEST(issued, 0) + GREATEST(-used, 0),
               used = GREATEST(used, 0) + GREATEST(-issued, 0)
         WHERE issued < 0 OR used < 0
        """,
    )
    # lines with both: keep the award on the line, move the consumption to a
    # copy of it
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO loyalty_history (
            card_id, order_id, order_model, description, issued, used,
            create_uid, create_date, write_uid, write_date
        )
        SELECT card_id, order_id, order_model, description, 0, used,
               create_uid, create_date, write_uid, write_date
          FROM loyalty_history
         WHERE issued > 0 AND used > 0
        """,
    )
    openupgrade.logged_query(
        cr,
        "UPDATE loyalty_history SET used = 0 WHERE issued > 0 AND used > 0",
    )


def _loyalty_history_points_changed_date(env):
    """New required field with default now(): the ORM would date every past
    movement to the migration. Use the creation date of the line."""
    cr = env.cr
    if openupgrade.column_exists(cr, "loyalty_history", "points_changed_date"):
        return
    openupgrade.add_columns(
        env,
        [
            (
                "loyalty.history",
                "points_changed_date",
                "datetime",
                None,
                "loyalty_history",
            )
        ],
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE loyalty_history
           SET points_changed_date = COALESCE(create_date, write_date, now())
        """,
    )


def _loyalty_card_balance_history(env):
    """loyalty.card.points is not stored anymore, v20 computes it from
    loyalty.history (sum of issued - used). Cards whose stored balance is not
    explained by their history (balance changed without a history line, e.g.
    a refund or an edit of the points) would silently change their balance.
    Add one balancing history line per such card so that every customer keeps
    exactly the points v19 showed. The stored column stays (legacy)."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "loyalty_card", "points"):
        return
    openupgrade.logged_query(
        cr,
        """
        WITH balance AS (
            SELECT card.id AS card_id,
                   card.points - COALESCE(SUM(h.issued - h.used), 0) AS diff
              FROM loyalty_card card
              LEFT JOIN loyalty_history h ON h.card_id = card.id
             GROUP BY card.id, card.points
        )
        INSERT INTO loyalty_history (
            card_id, description, issued, used, points_changed_date,
            create_uid, create_date, write_uid, write_date
        )
        SELECT balance.card_id,
               'Balance carried over from the stored card points'
               || ' (migration to Odoo 20)',
               GREATEST(balance.diff, 0),
               GREATEST(-balance.diff, 0),
               COALESCE(card.write_date, card.create_date, now()),
               1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
          FROM balance
          JOIN loyalty_card card ON card.id = balance.card_id
         WHERE ABS(balance.diff) >= 0.005
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _loyalty_history_cleanup(env)
    _loyalty_history_points_changed_date(env)
    _loyalty_card_balance_history(env)
