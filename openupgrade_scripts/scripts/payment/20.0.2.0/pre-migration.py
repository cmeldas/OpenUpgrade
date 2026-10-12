# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

_INSTALLED_STATES = ("installed", "to upgrade", "to install")


def _payment_provider_state(env):
    """payment.provider.state (disabled/enabled/test) is replaced by is_live
    and active (and is_published, which already existed).

    - enabled  -> active, live
    - test     -> active, not live
    - disabled -> not live; archived, unless the module of the provider is
      not installed: such providers are listed (active) to be installed, like
      in a new v20 database.
    """
    cr = env.cr
    if not openupgrade.column_exists(cr, "payment_provider", "state"):
        return
    for column in ("is_live", "active"):
        if not openupgrade.column_exists(cr, "payment_provider", column):
            openupgrade.logged_query(
                cr, f"ALTER TABLE payment_provider ADD COLUMN {column} BOOLEAN"
            )
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE payment_provider pp
           SET is_live = pp.state = 'enabled',
               active = pp.state IN ('enabled', 'test')
                   OR pp.code = 'none'
                   OR pp.module_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1
                         FROM ir_module_module imm
                        WHERE imm.id = pp.module_id
                          AND imm.state IN {_INSTALLED_STATES})
        """,
    )


def _payment_method_provider_id(env):
    """payment.method.provider_ids (many2many) -> provider_id (many2one,
    required, unique per code). In v20 the payment methods are defined by
    each provider module.

    - a method is kept for every provider it is linked to whose module is
      installed (or that has transactions/tokens with it); the first one keeps
      the record, the others get a copy (with its countries, currencies and
      image), the transactions and tokens follow their provider;
    - the XML-ID payment.payment_method_<x> of a kept method is moved to the
      module of its provider (payment_<provider>.payment_method_<x> is how v20
      provider modules define it), so that their data does not create a
      duplicate;
    - the other methods (only linked to providers whose module is not
      installed, i.e. the ~230 generic methods of v19) are deleted: they are
      recreated by the provider module when it gets installed.
    """
    cr = env.cr
    rel = "payment_method_payment_provider_rel"
    if not openupgrade.table_exists(cr, rel):
        return
    if not openupgrade.column_exists(cr, "payment_method", "provider_id"):
        openupgrade.logged_query(
            cr, "ALTER TABLE payment_method ADD COLUMN provider_id INTEGER"
        )
    cr.execute(
        f"""
        WITH pairs AS (
            SELECT payment_method_id AS method_id,
                   payment_provider_id AS provider_id,
                   FALSE AS used
              FROM {rel}
            UNION ALL
            SELECT payment_method_id, provider_id, TRUE
              FROM payment_transaction
             WHERE payment_method_id IS NOT NULL AND provider_id IS NOT NULL
            UNION ALL
            SELECT payment_method_id, provider_id, TRUE
              FROM payment_token
             WHERE payment_method_id IS NOT NULL AND provider_id IS NOT NULL
        )
        SELECT p.method_id, p.provider_id, imm.name
          FROM pairs p
          JOIN payment_provider pp ON pp.id = p.provider_id
          LEFT JOIN ir_module_module imm ON imm.id = pp.module_id
         GROUP BY p.method_id, p.provider_id, imm.name, imm.state, pp.module_id
        HAVING bool_or(p.used)
            OR pp.module_id IS NULL
            OR imm.state IN %s
         ORDER BY p.method_id, p.provider_id
        """,
        (_INSTALLED_STATES,),
    )
    providers_by_method = {}
    for method_id, provider_id, module in cr.fetchall():
        providers_by_method.setdefault(method_id, []).append((provider_id, module))
    cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = 'payment_method'
           AND column_name NOT IN ('id', 'provider_id')
        """
    )
    columns = ", ".join(f'"{row[0]}"' for row in cr.fetchall())
    cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = 'ir_attachment'
           AND column_name NOT IN ('id', 'res_id')
        """
    )
    attachment_columns = ", ".join(f'"{row[0]}"' for row in cr.fetchall())
    # (old method id, provider id) -> method id holding that pair
    method_for_pair = {}
    for method_id, providers in providers_by_method.items():
        cr.execute(
            "SELECT module, name FROM ir_model_data "
            "WHERE model = 'payment.method' AND res_id = %s ORDER BY id LIMIT 1",
            (method_id,),
        )
        xmlid = cr.fetchone()
        for index, (provider_id, provider_module) in enumerate(providers):
            if index == 0:
                new_id = method_id
                cr.execute(
                    "UPDATE payment_method SET provider_id = %s WHERE id = %s",
                    (provider_id, method_id),
                )
            else:
                cr.execute(
                    f"""
                    INSERT INTO payment_method ({columns}, provider_id)
                    SELECT {columns}, %s FROM payment_method WHERE id = %s
                    RETURNING id
                    """,
                    (provider_id, method_id),
                )
                new_id = cr.fetchone()[0]
                for table, column in (
                    ("payment_method_res_country_rel", "res_country_id"),
                    ("payment_method_res_currency_rel", "res_currency_id"),
                ):
                    if openupgrade.table_exists(cr, table):
                        cr.execute(
                            f"""
                            INSERT INTO {table} (payment_method_id, {column})
                            SELECT %s, {column} FROM {table}
                             WHERE payment_method_id = %s
                            """,
                            (new_id, method_id),
                        )
                cr.execute(
                    f"""
                    INSERT INTO ir_attachment ({attachment_columns}, res_id)
                    SELECT {attachment_columns}, %s
                      FROM ir_attachment
                     WHERE res_model = 'payment.method' AND res_id = %s
                       AND res_field IS NOT NULL
                    """,
                    (new_id, method_id),
                )
                for table in ("payment_transaction", "payment_token"):
                    cr.execute(
                        f"""
                        UPDATE {table} SET payment_method_id = %s
                         WHERE payment_method_id = %s AND provider_id = %s
                        """,
                        (new_id, method_id, provider_id),
                    )
            method_for_pair[(method_id, provider_id)] = new_id
            # XML-ID in the module of the provider, as defined in v20
            if (
                xmlid
                and xmlid[0] == "payment"
                and provider_module
                and provider_module not in ("payment", xmlid[0])
            ):
                cr.execute(
                    "SELECT 1 FROM ir_model_data WHERE module = %s AND name = %s",
                    (provider_module, xmlid[1]),
                )
                if cr.fetchone():
                    continue
                if index == 0:
                    cr.execute(
                        """
                        UPDATE ir_model_data SET module = %s
                         WHERE model = 'payment.method' AND res_id = %s
                           AND module = 'payment' AND name = %s
                        """,
                        (provider_module, method_id, xmlid[1]),
                    )
                else:
                    cr.execute(
                        """
                        INSERT INTO ir_model_data (module, name, model, res_id,
                            noupdate, create_uid, create_date, write_uid,
                            write_date)
                        VALUES (%s, %s, 'payment.method', %s, TRUE, 1,
                            now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC')
                        """,
                        (provider_module, xmlid[1], new_id),
                    )
    # the brands of a copied method point to the primary method of the same
    # provider
    for (method_id, provider_id), new_id in method_for_pair.items():
        if new_id == method_id:
            continue
        cr.execute(
            "SELECT primary_payment_method_id FROM payment_method WHERE id = %s",
            (new_id,),
        )
        primary_id = cr.fetchone()[0]
        if primary_id:
            cr.execute(
                "UPDATE payment_method SET primary_payment_method_id = %s "
                "WHERE id = %s",
                (method_for_pair.get((primary_id, provider_id)), new_id),
            )
    # methods kept for another provider than their primary method
    openupgrade.logged_query(
        cr,
        """
        UPDATE payment_method pm
           SET primary_payment_method_id = NULL
          FROM payment_method primary_pm
         WHERE primary_pm.id = pm.primary_payment_method_id
           AND primary_pm.provider_id IS DISTINCT FROM pm.provider_id
        """,
    )
    # never delete a method that is still referenced (transaction or token
    # without provider): keep it on one of its former providers
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE payment_method pm
           SET provider_id = (
               SELECT min(rel.payment_provider_id)
                 FROM {rel} rel
                WHERE rel.payment_method_id = pm.id)
         WHERE pm.provider_id IS NULL
           AND (EXISTS (SELECT 1 FROM payment_transaction tx
                         WHERE tx.payment_method_id = pm.id)
                OR EXISTS (SELECT 1 FROM payment_token tk
                            WHERE tk.payment_method_id = pm.id))
        """,
    )
    # methods of providers that are not installed
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM ir_model_data
         WHERE model = 'payment.method'
           AND res_id IN (SELECT id FROM payment_method WHERE provider_id IS NULL)
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM ir_attachment
         WHERE res_model = 'payment.method'
           AND res_id IN (SELECT id FROM payment_method WHERE provider_id IS NULL)
        """,
    )
    openupgrade.logged_query(
        cr, "DELETE FROM payment_method WHERE provider_id IS NULL"
    )


@openupgrade.migrate()
def migrate(env, version):
    _payment_provider_state(env)
    _payment_method_provider_id(env)
