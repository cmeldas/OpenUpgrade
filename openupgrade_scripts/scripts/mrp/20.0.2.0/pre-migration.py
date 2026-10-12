# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_fields = [
    ("mrp.bom", "mrp_bom", "product_uom_id", "uom_id"),
    ("mrp.bom.line", "mrp_bom_line", "product_uom_id", "uom_id"),
    ("mrp.bom.byproduct", "mrp_bom_byproduct", "product_uom_id", "uom_id"),
    ("mrp.production", "mrp_production", "product_uom_id", "uom_id"),
    (
        "mrp.production",
        "mrp_production",
        "location_final_id",
        "forecasted_location_id",
    ),
    ("mrp.unbuild", "mrp_unbuild", "product_uom_id", "uom_id"),
    (
        "mrp.workcenter.capacity",
        "mrp_workcenter_capacity",
        "product_uom_id",
        "uom_id",
    ),
]

# The lot label formats change their keys ('pdf'/'zpl' -> LOT_LABEL_FORMATS
# of stock), the values are mapped in post-migration from the legacy copies.
_copied_columns = {
    "stock_picking_type": [
        ("done_mrp_lot_label_to_print", None, None),
        ("generated_mrp_lot_label_to_print", None, None),
    ],
}

# Stored computed fields that are new or become stored: pre-create them to
# avoid the ORM computing them on all the manufacturing orders.
_added_columns = [
    ("mrp.production", "duration", "float", None, "mrp_production", "float8"),
    (
        "mrp.production",
        "duration_expected",
        "float",
        None,
        "mrp_production",
        "float8",
    ),
    ("mrp.production", "note", "html", None, "mrp_production"),
]


def fill_mrp_production_group_magic_columns(env):
    """The v19 migration inserted the production groups without the magic
    columns (create/write date and uid). Take them from the oldest
    manufacturing order of the group, as the group was created for it."""
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE mrp_production_group mpg
        SET create_date = COALESCE(mpg.create_date, sub.create_date),
            create_uid = COALESCE(mpg.create_uid, sub.create_uid),
            write_date = COALESCE(mpg.write_date, sub.create_date),
            write_uid = COALESCE(mpg.write_uid, sub.create_uid)
        FROM (
            SELECT DISTINCT ON (mp.production_group_id)
                mp.production_group_id, mp.create_date, mp.create_uid
            FROM mrp_production mp
            WHERE mp.production_group_id IS NOT NULL
            ORDER BY mp.production_group_id, mp.create_date, mp.id
        ) AS sub
        WHERE sub.production_group_id = mpg.id
            AND (mpg.create_date IS NULL OR mpg.write_date IS NULL
                OR mpg.create_uid IS NULL OR mpg.write_uid IS NULL)
        """,
    )
    # groups without any manufacturing order
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE mrp_production_group
        SET create_date = COALESCE(create_date, NOW() AT TIME ZONE 'UTC'),
            create_uid = COALESCE(create_uid, 1),
            write_date = COALESCE(write_date, create_date,
                NOW() AT TIME ZONE 'UTC'),
            write_uid = COALESCE(write_uid, create_uid, 1)
        WHERE create_date IS NULL OR write_date IS NULL
            OR create_uid IS NULL OR write_uid IS NULL
        """,
    )


def rename_import_mappings(env):
    """openupgradelib.rename_fields does not touch base_import.mapping (the
    column -> field memory of the import dialog). Rename the field there too,
    otherwise base_import drops the mapping as stale (production: the
    'product_uom_id' column of the manufacturing order import)."""
    if not openupgrade.table_exists(env.cr, "base_import_mapping"):
        return
    for model, _table, old_field, new_field in _renamed_fields:
        openupgrade.logged_query(
            env.cr,
            """
            UPDATE base_import_mapping
            SET field_name = %(new)s || substr(field_name, length(%(old)s) + 1)
            WHERE res_model = %(model)s
                AND (field_name = %(old)s
                    OR left(field_name, length(%(old)s) + 1) = %(old)s || '/')
            """,
            {"new": new_field, "old": old_field, "model": model},
        )


def fill_mrp_production_durations(env):
    """mrp.production duration and duration_expected are now stored: they are
    the sums of the work orders' values."""
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE mrp_production mp
        SET duration = COALESCE(sub.duration, 0.0),
            duration_expected = COALESCE(sub.duration_expected, 0.0)
        FROM mrp_production mp2
        LEFT JOIN (
            SELECT production_id,
                SUM(duration) AS duration,
                SUM(duration_expected) AS duration_expected
            FROM mrp_workorder
            GROUP BY production_id
        ) AS sub ON sub.production_id = mp2.id
        WHERE mp2.id = mp.id
            AND mp.duration IS NULL
            AND mp.duration_expected IS NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    fill_mrp_production_group_magic_columns(env)
    openupgrade.rename_fields(env, _renamed_fields)
    rename_import_mappings(env)
    openupgrade.copy_columns(env.cr, _copied_columns)
    openupgrade.add_columns(env, _added_columns)
    fill_mrp_production_durations(env)
