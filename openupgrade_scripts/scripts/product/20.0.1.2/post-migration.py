# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2 import sql


def _newly_translatable_names(env):
    """product.category.name and product.combo.name are translatable now.

    The ORM converts the column to {"en_US": <old value>}. Add the same text
    for the other active languages, so that the record does not depend on the
    en_US fallback and a later edit in one language does not look like a
    translation of the other ones.
    """
    for table in ("product_category", "product_combo"):
        openupgrade.logged_query(
            env.cr,
            sql.SQL(
                """
                UPDATE {table} t
                SET name = t.name || (
                    SELECT jsonb_object_agg(lang.code, t.name ->> 'en_US')
                    FROM res_lang lang
                    WHERE lang.active AND lang.code != 'en_US'
                        AND NOT t.name ? lang.code
                )
                WHERE jsonb_typeof(t.name) = 'object'
                    AND t.name ? 'en_US'
                    AND EXISTS (
                        SELECT 1 FROM res_lang lang
                        WHERE lang.active AND lang.code != 'en_US'
                            AND NOT t.name ? lang.code
                    )
                """
            ).format(table=sql.Identifier(table)),
        )


def _ptav_show_price_extra(env):
    """19.0 showed the extra price of every attribute value on the website,
    20.0 only when show_price_extra is set on the value.
    """
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE product_template_attribute_value
        SET show_price_extra = TRUE
        WHERE COALESCE(price_extra, 0) != 0
            AND show_price_extra IS NOT TRUE
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _newly_translatable_names(env)
    _ptav_show_price_extra(env)
