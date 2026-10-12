# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

_deleted_xmlids = [
    # noupdate module categories removed in v20 (Odoo never deletes noupdate
    # records by itself)
    "base.module_category_accounting_reporting",
    "base.module_category_human_resources_payroll",
    "base.module_category_manufacturing_manufacturing",
    "base.module_category_manufacturing",
    "base.module_category_services_employee_hourly_cost",
    "base.module_category_supply_chain_repair",
    "base.module_category_tools_ui",
]


# countries whose (translatable) vat_label is changed by noupdate_changes.xml
_vat_label_changed = ["br", "ci", "hk", "id", "kr", "ph", "us"]


def _res_country_zip_applicability(env):
    """zip_required (boolean) -> zip_applicability (required/optional/
    not_applicable, default required). noupdate_changes.xml sets the v20
    values of the standard countries; a zip that was not required before is
    at least optional."""
    if not openupgrade.column_exists(env.cr, "res_country", "zip_required"):
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE res_country
           SET zip_applicability = 'optional'
         WHERE zip_required IS NOT TRUE AND zip_applicability = 'required'
        """,
    )


def _ir_mail_server_max_email_size(env):
    """ir.mail_server.max_email_size was removed, only the system parameter
    base.default_max_email_size (default raised from 10 to 20 MB) is left."""
    cr = env.cr
    if openupgrade.column_exists(cr, "ir_mail_server", "max_email_size"):
        cr.execute(
            """
            SELECT max(max_email_size) FROM ir_mail_server
             WHERE active AND max_email_size > 0
            """
        )
        max_size = cr.fetchone()[0]
        if max_size:
            # keep the most permissive limit that was configured on a server
            openupgrade.logged_query(
                cr,
                """
                UPDATE ir_config_parameter SET value = %(size)s
                 WHERE key = 'base.default_max_email_size'
                """,
                {"size": str(max_size)},
            )
            return
    # the previous default value was never changed: take the new default
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_config_parameter SET value = '20'
         WHERE key = 'base.default_max_email_size' AND value = '10'
        """,
    )


def _res_partner_bank(env):
    """res.bank was removed: the bank name, BIC and address are now plain
    fields of res.partner.bank. The legacy column bank_id and the table
    res_bank are kept."""
    cr = env.cr
    if not (
        openupgrade.table_exists(cr, "res_bank")
        and openupgrade.column_exists(cr, "res_partner_bank", "bank_id")
    ):
        return
    # computations queued by the ORM for the new columns must not overwrite
    # the values written below
    env.flush_all()
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_partner_bank rpb
           SET bank_name = COALESCE(rpb.bank_name, rb.name),
               bank_bic = COALESCE(rpb.bank_bic, upper(rb.bic)),
               street = COALESCE(rpb.street, rb.street),
               street2 = COALESCE(rpb.street2, rb.street2),
               zip = COALESCE(rpb.zip, rb.zip),
               city = COALESCE(rpb.city, rb.city),
               state_id = COALESCE(rpb.state_id, rb.state)
          FROM res_bank rb
         WHERE rpb.bank_id = rb.id
        """,
    )
    # country_id is the country of the bank address in v20; the ORM
    # precomputed it from the partner (or company) when the column was
    # created. Prefer the country of the bank, consistent with state_id, and
    # recompute the clearing label that depends on it.
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_partner_bank rpb
           SET country_id = rb.country
          FROM res_bank rb
         WHERE rpb.bank_id = rb.id AND rb.country IS NOT NULL
           AND rpb.country_id IS DISTINCT FROM rb.country
        RETURNING rpb.id
        """,
    )
    bank_account_ids = [row[0] for row in cr.fetchall()]
    if bank_account_ids:
        accounts = (
            env["res.partner.bank"]
            .with_context(active_test=False)
            .browse(bank_account_ids)
        )
        accounts.invalidate_recordset(["country_id"])
        env.add_to_compute(accounts._fields["clearing_label_id"], accounts)
        accounts._recompute_recordset(["clearing_label_id"])
    # holder_name is a stored compute (partner name when empty) on a renamed
    # column, so the ORM does not compute it for existing accounts
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_partner_bank rpb
           SET holder_name = rp.name
          FROM res_partner rp
         WHERE rpb.partner_id = rp.id
           AND (rpb.holder_name IS NULL OR rpb.holder_name = '')
        """,
    )


def _res_partner_additional_identifiers(env):
    """company_registry (base) and global_location_number (account_add_gln)
    are now keys of the json additional_identifiers."""
    cr = env.cr
    Partner = env["res.partner"]
    metadata = Partner._get_all_additional_identifiers_metadata()
    en_keys_by_country = {}
    for key, vals in sorted(
        metadata.items(), key=lambda item: item[1].get("sequence", 1000)
    ):
        if vals.get("category") != "EN" or key == "OTHER":
            continue
        for country_code in vals.get("countries") or []:
            en_keys_by_country.setdefault(country_code, key)
    if openupgrade.column_exists(cr, "res_partner", "company_registry"):
        # country of the identifier: the partner's, else the prefix of its
        # VAT, else the country of its company or of the only company
        cr.execute(
            """
            SELECT rp.id, trim(rp.company_registry),
                   COALESCE(
                       rc.code,
                       upper(substring(rp.vat FROM '^[A-Za-z]{2}')),
                       (SELECT ccc.code FROM res_company cc
                          JOIN res_partner ccp ON ccp.id = cc.partner_id
                          JOIN res_country ccc ON ccc.id = ccp.country_id
                         WHERE cc.id = rp.company_id),
                       (SELECT min(ccc.code) FROM res_company cc
                          JOIN res_partner ccp ON ccp.id = cc.partner_id
                          JOIN res_country ccc ON ccc.id = ccp.country_id
                        HAVING count(*) = 1)
                   )
              FROM res_partner rp
              LEFT JOIN res_country rc ON rc.id = rp.country_id
             WHERE trim(COALESCE(rp.company_registry, '')) != ''
            """
        )
        for partner_id, value, country_code in cr.fetchall():
            key = en_keys_by_country.get(country_code, "OTHER")
            cr.execute(
                """
                UPDATE res_partner
                   SET additional_identifiers =
                       jsonb_build_object(%(key)s, %(value)s)
                       || COALESCE(additional_identifiers, '{}'::jsonb)
                 WHERE id = %(id)s
                """,
                {"key": key, "value": value, "id": partner_id},
            )
            _logger.info(
                "res.partner %s: company_registry %s -> additional_identifiers[%s]",
                partner_id,
                value,
                key,
            )
    if openupgrade.column_exists(cr, "res_partner", "global_location_number"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE res_partner
               SET additional_identifiers =
                   jsonb_build_object('EAN_GLN', trim(global_location_number))
                   || COALESCE(additional_identifiers, '{}'::jsonb)
             WHERE trim(COALESCE(global_location_number, '')) != ''
            """,
        )
    Partner.invalidate_model(["additional_identifiers"])


def _res_company_logo_web(env):
    """logo_web is now stored as an attachment (and binary values are raw
    instead of base64): compute it again from the partner image."""
    companies = env["res.company"].with_context(active_test=False).search([])
    env.add_to_compute(companies._fields["logo_web"], companies)
    companies._recompute_recordset(["logo_web"])


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "base", "20.0.1.3/noupdate_changes.xml")
    # load_data only writes en_US: drop the other languages of the changed
    # translatable field, the translations are loaded again after this script
    openupgrade.delete_record_translations(
        env.cr, "base", _vat_label_changed, field_list=["vat_label"]
    )
    _res_country_zip_applicability(env)
    _ir_mail_server_max_email_size(env)
    _res_partner_bank(env)
    _res_partner_additional_identifiers(env)
    _res_company_logo_web(env)
    openupgrade.delete_records_safely_by_xml_id(env, _deleted_xmlids)
