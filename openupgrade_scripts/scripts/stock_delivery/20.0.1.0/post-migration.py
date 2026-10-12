# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2 import sql


def _product_hs_code_country_of_origin(env):
    """hs_code and country_of_origin are stored on product.product now, the
    template fields are computed from the variant. Copy the template values
    (kept in the template columns, which are not dropped) to the variants.
    """
    cr = env.cr
    for column in ("hs_code", "country_of_origin"):
        if not openupgrade.column_exists(cr, "product_template", column):
            continue
        openupgrade.logged_query(
            cr,
            sql.SQL(
                """
                UPDATE product_product pp
                SET {column} = pt.{column}
                FROM product_template pt
                WHERE pp.product_tmpl_id = pt.id
                    AND pt.{column} IS NOT NULL
                    AND pp.{column} IS NULL
                """
            ).format(column=sql.Identifier(column)),
        )


@openupgrade.migrate()
def migrate(env, version):
    _product_hs_code_country_of_origin(env)
