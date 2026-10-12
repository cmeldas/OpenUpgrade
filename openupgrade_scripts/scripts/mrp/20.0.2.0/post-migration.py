# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# v19 printed one label per lot/SN, in PDF (4 x 12 sheet) or ZPL
_lot_label_mapping = [("pdf", "4x12_lots"), ("zpl", "zpl_lots")]


def map_lot_label_formats(env):
    for field in ("done_mrp_lot_label_to_print", "generated_mrp_lot_label_to_print"):
        legacy = openupgrade.get_legacy_name(field)
        if not openupgrade.column_exists(env.cr, "stock_picking_type", legacy):
            continue
        openupgrade.map_values(
            env.cr,
            legacy,
            field,
            _lot_label_mapping,
            table="stock_picking_type",
        )


def link_scrap_moves_to_production(env):
    """stock.scrap is gone in v20, scraps are stock moves with is_scrap. In v19
    the scrap move of a manufacturing order scrap already carried the
    production_id / raw_material_production_id, this only fills the moves
    where it is missing (same rule as v20 _prepare_scrap_move_vals)."""
    cr = env.cr
    if not openupgrade.table_exists(cr, "stock_scrap") or not (
        openupgrade.column_exists(cr, "stock_scrap", "production_id")
    ):
        return
    scrap_column = next(
        (
            column
            for column in ("scrap_id", openupgrade.get_legacy_name("scrap_id"))
            if openupgrade.column_exists(cr, "stock_move", column)
        ),
        None,
    )
    if not scrap_column:
        _logger.warning(
            "stock_move has no scrap_id column: scrap moves of manufacturing "
            "orders were not checked"
        )
        return
    # the finished products of the MO are its non-scrap finished moves
    # (v20 move_finished_ids has the domain is_scrap = False)
    finished_filter = (
        "AND fm.is_scrap IS NOT TRUE"
        if openupgrade.column_exists(cr, "stock_move", "is_scrap")
        else ""
    )
    query = """
        WITH scrap AS (
            SELECT ss.id,
                COALESCE(ss.production_id, wo.production_id) AS production_id
            FROM stock_scrap ss
            LEFT JOIN mrp_workorder wo ON wo.id = ss.workorder_id
            WHERE ss.production_id IS NOT NULL OR ss.workorder_id IS NOT NULL
        )
        UPDATE stock_move sm
        SET {target} = scrap.production_id
        FROM scrap
        WHERE sm.{scrap_column} = scrap.id
            AND scrap.production_id IS NOT NULL
            AND sm.production_id IS NULL
            AND sm.raw_material_production_id IS NULL
            AND {condition} EXISTS (
                SELECT 1 FROM stock_move fm
                WHERE fm.production_id = scrap.production_id
                    AND fm.product_id = sm.product_id
                    AND fm.id != sm.id
                    {finished_filter}
            )
    """
    # finished product scrapped -> production_id, component -> raw_material
    for target, condition in (
        ("production_id", ""),
        ("raw_material_production_id", "NOT"),
    ):
        openupgrade.logged_query(
            cr,
            query.format(
                target=target,
                condition=condition,
                scrap_column=scrap_column,
                finished_filter=finished_filter,
            ),
        )


@openupgrade.migrate()
def migrate(env, version):
    map_lot_label_formats(env)
    if openupgrade.column_exists(env.cr, "mrp_unbuild", "lot_id"):
        openupgrade.m2o_to_x2m(
            env.cr, env["mrp.unbuild"], "mrp_unbuild", "lot_ids", "lot_id"
        )
    link_scrap_moves_to_production(env)
