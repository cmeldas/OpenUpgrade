# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2 import sql


def _scrap_reason_tags(env):
    """Copy stock.scrap.scrap_reason_tag_ids to the scrap moves."""
    cr = env.cr
    if not openupgrade.table_exists(
        cr, "stock_scrap_stock_scrap_reason_tag_rel"
    ) or not openupgrade.column_exists(cr, "stock_move", "scrap_id"):
        return
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO stock_move_stock_scrap_reason_tag_rel
            (stock_move_id, stock_scrap_reason_tag_id)
        SELECT DISTINCT sm.id, rel.stock_scrap_reason_tag_id
        FROM stock_scrap_stock_scrap_reason_tag_rel rel
        JOIN stock_move sm ON sm.scrap_id = rel.stock_scrap_id
        ON CONFLICT DO NOTHING
        """,
    )


def _stock_scrap_legacy(env):
    """The model stock.scrap is removed at the end of the migration.

    - Its table stays (the framework never drops tables) as the legacy
      mapping scrap -> move (stock_move.scrap_id is kept as well), but its
      outgoing foreign keys (several of them ON DELETE RESTRICT) would block
      deleting products, locations, units or companies forever, so drop them.
    - Removing the model may delete its chatter (mail's ir.model ondelete
      method; the framework skips it on forced deletions, other versions of
      the framework may not). stock.move has no chatter in 20.0, so the
      messages ("Scrap created", state tracking) cannot be moved to the scrap
      moves; keep a copy in legacy tables whatever happens to the originals.
    """
    cr = env.cr
    if not openupgrade.table_exists(cr, "stock_scrap"):
        return
    cr.execute(
        """
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'stock_scrap'::regclass AND contype = 'f'
        """
    )
    for (constraint,) in cr.fetchall():
        openupgrade.logged_query(
            cr,
            sql.SQL("ALTER TABLE stock_scrap DROP CONSTRAINT IF EXISTS {}").format(
                sql.Identifier(constraint)
            ),
        )
    messages_table = openupgrade.get_legacy_name("stock_scrap_mail_message")
    if not openupgrade.table_exists(cr, messages_table):
        openupgrade.logged_query(
            cr,
            sql.SQL(
                """
                CREATE TABLE {} AS
                SELECT * FROM mail_message WHERE model = 'stock.scrap'
                """
            ).format(sql.Identifier(messages_table)),
        )
    tracking_table = openupgrade.get_legacy_name("stock_scrap_mail_tracking_value")
    if not openupgrade.table_exists(cr, tracking_table) and openupgrade.table_exists(
        cr, "mail_tracking_value"
    ):
        openupgrade.logged_query(
            cr,
            sql.SQL(
                """
                CREATE TABLE {} AS
                SELECT mtv.* FROM mail_tracking_value mtv
                JOIN mail_message mm ON mm.id = mtv.mail_message_id
                WHERE mm.model = 'stock.scrap'
                """
            ).format(sql.Identifier(tracking_table)),
        )


def _stock_move_line_date(env):
    """stock.move.line.date is now a stored related to move_id.date; align the
    few lines whose own date differed (by up to a second in production).
    """
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE stock_move_line sml
        SET date = sm.date
        FROM stock_move sm
        WHERE sm.id = sml.move_id AND sml.date IS DISTINCT FROM sm.date
        """,
    )


def _company_picking_policy(env):
    """The shipping policy default of sale orders was an ir.default
    (sale_stock settings), in 20.0 it is res.company.picking_policy and the
    sale order default reads it. Move the value, and drop the ir.default,
    which would otherwise keep overriding the company setting.
    """
    cr = env.cr
    # a company specific default wins over a global one
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_company rc
        SET picking_policy = company_default.policy
        FROM (
            SELECT DISTINCT ON (company.id)
                company.id AS company_id,
                idf.json_value::jsonb #>> '{}' AS policy
            FROM res_company company
            JOIN ir_default idf
                ON idf.company_id = company.id OR idf.company_id IS NULL
            JOIN ir_model_fields imf ON imf.id = idf.field_id
            WHERE imf.model = 'sale.order' AND imf.name = 'picking_policy'
                AND idf.user_id IS NULL AND idf.condition IS NULL
                AND idf.json_value::jsonb #>> '{}' IN ('direct', 'one')
            ORDER BY company.id, idf.company_id NULLS LAST
        ) AS company_default
        WHERE company_default.company_id = rc.id
            AND rc.picking_policy IS DISTINCT FROM company_default.policy
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM ir_default idf
        USING ir_model_fields imf
        WHERE imf.id = idf.field_id
            AND imf.model = 'sale.order' AND imf.name = 'picking_policy'
            AND idf.user_id IS NULL AND idf.condition IS NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    # The translations of the delivery confirmation body are kept on purpose:
    # body_html is translate=True, so the other languages keep their 19.0
    # text, and the 20.0 .po files have no translation of the new text yet
    # (delete_record_translations would fall back to English).
    openupgrade.load_data(env, "stock", "20.0.1.1/noupdate_changes.xml")
    _scrap_reason_tags(env)
    _stock_scrap_legacy(env)
    _stock_move_line_date(env)
    _company_picking_policy(env)
