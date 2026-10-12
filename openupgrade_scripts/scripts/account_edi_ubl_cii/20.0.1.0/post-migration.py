# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from lxml import etree
from openupgradelib import openupgrade

from odoo.tools.misc import file_open


def _uom_unece_code(env):
    """data/uom_data.xml (noupdate) sets uom.uom.unece_code on records of the
    uom module: as these records exist, the update skips them. Set the codes
    on the existing units that have none (by SQL: a missing record must not
    be created, a unit may have been deleted)."""
    with file_open("account_edi_ubl_cii/data/uom_data.xml", "rb") as fp:
        tree = etree.parse(fp)
    for record in tree.xpath("//record[@model='uom.uom']"):
        field = record.find("field[@name='unece_code']")
        if field is None or not field.text:
            continue
        uom = env.ref(record.get("id"), raise_if_not_found=False)
        if not uom:
            continue
        env.cr.execute(
            """
            UPDATE uom_uom SET unece_code = %s
             WHERE id = %s AND unece_code IS NULL
               AND NOT EXISTS (SELECT 1 FROM uom_uom WHERE unece_code = %s)
            """,
            (field.text.strip(), uom.id, field.text.strip()),
        )
    env["uom.uom"].invalidate_model(["unece_code"])


@openupgrade.migrate()
def migrate(env, version):
    _uom_unece_code(env)
