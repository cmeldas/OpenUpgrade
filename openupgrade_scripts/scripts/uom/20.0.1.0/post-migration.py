# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def _fix_square_foot(env):
    """Repair data of DBs migrated with the 19.0 uom scripts.

    19.0 pre-migration renamed uom.uom_square_foot to
    uom.product_uom_square_foot, the analysis listed it as NEW + DEL and the
    record is noupdate, so ft² was left without relative_uom_id and with the
    factor of 18.0. Write it through the ORM so that factor and parent_path
    get recomputed.
    """
    square_foot = env.ref("uom.product_uom_square_foot", raise_if_not_found=False)
    square_meter = env.ref("uom.product_uom_square_meter", raise_if_not_found=False)
    if square_foot and square_meter and not square_foot.relative_uom_id:
        square_foot.write(
            {"relative_uom_id": square_meter.id, "relative_factor": 0.092903}
        )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "uom", "20.0.1.0/noupdate_changes.xml")
    _fix_square_foot(env)
