# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_fields = [
    ("purchase.order.line", "purchase_order_line", "product_uom_id", "uom_id"),
]


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.rename_fields(env, _renamed_fields)
    # Odoo moved these views out of the noupdate block during 19.0
    # (OpenUpgrade e9c1479fa95); DBs migrated before that fix still have
    # noupdate=True and would never get the 20.0 version.
    openupgrade.set_xml_ids_noupdate_value(
        env,
        "purchase",
        ["track_po_line_qty_received_template", "track_po_line_template"],
        False,
    )
