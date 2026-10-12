# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging

from openupgradelib import openupgrade
from psycopg2.extras import Json

from odoo.fields import Command

_logger = logging.getLogger(__name__)

# res.company fields that are computed from ir.default in v20
# (`company_default_for`) and were plain stored fields in v19
_company_default_fields = [
    ("income_account_id", "product.category", "property_account_income_categ_id"),
    ("expense_account_id", "product.category", "property_account_expense_categ_id"),
    (
        "account_stock_valuation_id",
        "product.category",
        "property_stock_valuation_account_id",
    ),
    ("account_stock_journal_id", "product.category", "property_stock_journal"),
    ("cost_method", "product.category", "property_cost_method"),
    ("inventory_valuation", "product.category", "property_valuation"),
]

# Charts of accounts whose account.group template was converted to parent
# accounts by Odoo (odoo/upgrade_code/19.3-00-account-groups.py,
# COA_TO_MIGRATE). The account.group templates of all the other charts were
# dropped without replacement in v20: their groups are not converted, so that
# migrated databases look like new ones (the table stays as
# openupgrade_legacy_20_0_account_group).
_COA_WITH_PARENT_ACCOUNTS = {
    "ar_base",
    "ar_ex",
    "ar_ri",
    "bd",
    "bh",
    "co",
    "do",
    "ec",
    "il",
    "iq",
    "jo_standard",
    "kh",
    "kw",
    "lb",
    "mx",
    "mz",
    "om",
    "pk",
    "qa",
    "sk",
    "ua_psbo",
    "us",
    "zm",
}


def _get_account_group_table(cr):
    if openupgrade.table_exists(cr, "account_group"):
        return "account_group"
    legacy_table = openupgrade.get_legacy_name("account_group")
    if openupgrade.table_exists(cr, legacy_table):
        return legacy_table
    return False


def _group_contains(start, end, code):
    return bool(code) and start <= code[: len(start)] <= end


def _get_groups_to_convert(cr, table):
    """Groups converted to parent accounts:
    - the groups of the companies whose chart template got parent accounts in
      v20 (_COA_WITH_PARENT_ACCOUNTS);
    - the groups created by users (no XML-ID), whatever the chart.
    """
    chart_clause = "FALSE"
    if openupgrade.column_exists(cr, "res_company", "chart_template"):
        chart_clause = """
            COALESCE(root.chart_template, rc.chart_template) IN %(charts)s
        """
    cr.execute(
        f"""
        SELECT g.id, g.company_id, g.code_prefix_start,
               COALESCE(NULLIF(g.code_prefix_end, ''), g.code_prefix_start),
               g.name, g.parent_id
          FROM {table} g
          JOIN res_company rc ON rc.id = g.company_id
          LEFT JOIN res_company root
            ON root.id = split_part(rc.parent_path, '/', 1)::integer
         WHERE COALESCE(g.code_prefix_start, '') != ''
           AND ({chart_clause}
                OR NOT EXISTS (
                    SELECT 1 FROM ir_model_data imd
                     WHERE imd.model = 'account.group' AND imd.res_id = g.id))
         ORDER BY g.company_id, length(g.code_prefix_start),
                  g.code_prefix_start, g.id
        """,
        {"charts": tuple(_COA_WITH_PARENT_ACCOUNTS)},
    )
    return cr.fetchall()


def _convert_account_groups(env):
    """account.group was replaced by parent accounts (account.account.parent_id).

    Same semantics as odoo/upgrade_code/19.3-00-account-groups.py (used by
    Odoo to convert the chart templates), applied to the groups returned by
    `_get_groups_to_convert`:
    - each group becomes an archived account, named like the group, with the
      code 'start' (or 'start-end'), or no code when an account already has
      exactly that code, and the account type of the last account it covers;
    - groups covering no account are skipped (they would have no type);
    - the parent of a group account (or of an existing account) is the
      account of the most specific converted group containing its prefix
      (or code);
    - the XML-IDs of the groups are kept on the new accounts (the chart
      templates of v20 define these parent accounts with the same XML-IDs).
    The remaining XML-IDs of account.group are deleted; the original table is
    kept as openupgrade_legacy_20_0_account_group by the pre-migration.
    """
    cr = env.cr
    table = _get_account_group_table(cr)
    if not table:
        return
    cr.execute("SELECT 1 FROM account_account WHERE parent_id IS NOT NULL LIMIT 1")
    if cr.fetchone():
        # parent_id is new in v20: already converted
        return
    groups = _get_groups_to_convert(cr, table)
    if not groups:
        _logger.info("No account group to convert to parent accounts")
        return
    Account = env["account.account"].with_context(
        active_test=False,
        tracking_disable=True,
        mail_create_nolog=True,
        mail_create_nosubscribe=True,
        mail_notrack=True,
    )
    group_to_account = {}
    account_parent = {}
    groups_by_company = {}
    for group in groups:
        groups_by_company.setdefault(group[1], []).append(group)
    for company_id, company_groups in groups_by_company.items():
        company = env["res.company"].browse(company_id).exists()
        if not company:
            continue
        root_id = str(company.root_id.id)
        cr.execute(
            """
            SELECT a.id, a.code_store->>%(root)s, a.account_type
              FROM account_account a
              JOIN account_account_res_company_rel rel
                ON rel.account_account_id = a.id
             WHERE rel.res_company_id = %(company)s
               AND a.code_store->>%(root)s IS NOT NULL
             ORDER BY a.code_store->>%(root)s, a.id
            """,
            {"root": root_id, "company": company_id},
        )
        accounts = cr.fetchall()
        codes = {code for _id, code, _type in accounts}
        converted = []  # (start, end, account id), shortest prefixes first
        # groups are sorted by prefix length, so parents are created first
        for group_id, _cid, start, end, name, _parent_id in company_groups:
            if len(start) != len(end) or start > end:
                _logger.warning("Skipping account group %s: invalid prefixes", group_id)
                continue
            covered = [a for a in accounts if _group_contains(start, end, a[1])]
            if not covered:
                _logger.info(
                    "Account group %s (%s) covers no account: not converted",
                    group_id,
                    start,
                )
                continue
            code = start if start == end else f"{start}-{end}"
            if code in codes:
                code = False
            # parent: the most specific converted group containing this one
            # (like find_parent_id of the upgrade_code)
            parent_account_id = False
            for p_start, p_end, p_account_id in converted:
                if len(p_start) < len(start) and _group_contains(
                    p_start, p_end, start
                ):
                    parent_account_id = p_account_id
            vals = {
                "name": (name or {}).get("en_US") or start,
                "code": code,
                "account_type": covered[-1][2],
                "active": False,
                "company_ids": [Command.set([company_id])],
                "parent_id": parent_account_id,
            }
            new_account = Account.with_company(company_id).create(vals)
            group_to_account[group_id] = new_account.id
            converted.append((start, end, new_account.id))
            if code:
                codes.add(code)
            # keep all the translations of the group name
            cr.execute(
                "UPDATE account_account SET name = %s WHERE id = %s",
                (Json(name or {"en_US": start}), new_account.id),
            )
        # most specific converted group of each existing account
        groups_by_length = sorted(
            (g for g in company_groups if g[0] in group_to_account),
            key=lambda g: (-len(g[2]), g[0]),
        )
        for account_id, code, _type in accounts:
            for group in groups_by_length:
                if _group_contains(group[2], group[3], code):
                    account_parent[account_id] = group_to_account[group[0]]
                    break
    env.flush_all()
    for account_id, parent_account_id in account_parent.items():
        cr.execute(
            """
            UPDATE account_account
               SET parent_id = %s
             WHERE id = %s AND parent_id IS NULL
            """,
            (parent_account_id, account_id),
        )
    env["account.account"].invalidate_model(["parent_id", "parent_path", "name"])
    env["account.account"]._parent_store_compute()
    # XML-IDs of the chart template groups now point to the parent accounts
    for group_id, account_id in group_to_account.items():
        cr.execute(
            """
            UPDATE ir_model_data
               SET model = 'account.account', res_id = %s
             WHERE model = 'account.group' AND res_id = %s
            """,
            (account_id, group_id),
        )
    _logger.info(
        "%s account groups converted to parent accounts, %s accounts attached",
        len(group_to_account),
        len(account_parent),
    )
    env.invalidate_all()


def _company_defaults_to_ir_default(env):
    """In v20 these res.company fields are computed from (and written to)
    ir.default of the product.category company dependent fields. v19 stored
    them on the company and normally created the same ir.default; add the
    missing ones so that no company loses its default account/journal."""
    cr = env.cr
    for company_field, model, field in _company_default_fields:
        if not openupgrade.column_exists(cr, "res_company", company_field):
            continue
        openupgrade.logged_query(
            cr,
            f"""
            INSERT INTO ir_default (field_id, company_id, json_value,
                                    create_uid, create_date, write_uid, write_date)
            SELECT imf.id, rc.id, to_jsonb(rc.{company_field})::text,
                   1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
              FROM res_company rc
              JOIN ir_model_fields imf
                ON imf.model = %s AND imf.name = %s
             WHERE rc.{company_field} IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                     FROM ir_default d
                    WHERE d.field_id = imf.id
                      AND d.company_id = rc.id
                      AND d.user_id IS NULL
                      AND d.condition IS NULL
               )
            """,
            (model, field),
        )
    env["ir.default"].invalidate_model()
    env.transaction.invalidate_ormcache()
    env["res.company"].invalidate_model(
        [fname for fname, _model, _field in _company_default_fields]
    )


def _account_journal_group(env):
    """v19: a ledger group contains every journal of its company except its
    excluded_journal_ids (account.journal.journal_group_ids is the same
    relation table, i.e. the groups EXCLUDING the journal). v20: a journal
    belongs to at most one ledger (journal_group_id) and journals without
    ledger post to the statutory ledger.

    Only the unambiguous case is converted: when a company has several ledger
    groups, a journal included in exactly one of them (excluded from all the
    others) is a journal specific to that ledger. Journals included in all the
    groups (or in the only group) stay in the statutory ledger; the other
    configurations are logged to be set manually.
    """
    cr = env.cr
    rel = "account_journal_account_journal_group_rel"
    if not openupgrade.table_exists(cr, rel) or not openupgrade.column_exists(
        cr, "account_journal", "journal_group_id"
    ):
        return
    company_clause = (
        "AND (g.company_id IS NULL OR g.company_id = j.company_id)"
        if openupgrade.column_exists(cr, "account_journal_group", "company_id")
        else ""
    )
    cr.execute(
        f"""
        SELECT j.id,
               array_agg(g.id ORDER BY g.id) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM {rel} r
                    WHERE r.account_journal_id = j.id
                      AND r.account_journal_group_id = g.id)),
               count(g.id)
          FROM account_journal j
          JOIN account_journal_group g ON TRUE {company_clause}
         WHERE j.journal_group_id IS NULL
         GROUP BY j.id
        """
    )
    for journal_id, included_ids, nb_groups in cr.fetchall():
        included_ids = included_ids or []
        if nb_groups > 1 and len(included_ids) == 1:
            cr.execute(
                "UPDATE account_journal SET journal_group_id = %s WHERE id = %s",
                (included_ids[0], journal_id),
            )
        elif 1 < len(included_ids) < nb_groups:
            _logger.warning(
                "Journal %s was part of the ledger groups %s: v20 allows only "
                "one ledger per journal, set it manually",
                journal_id,
                included_ids,
            )


def _mail_template_partner_bank_fields(env):
    """res.partner.bank.acc_number / acc_holder_name were renamed (base). The
    body of the v19 invoice template (noupdate, translated) still uses them,
    in all languages, which would break the rendering of the invoice email."""
    for old, new in (
        ("partner_bank_id.acc_number", "partner_bank_id.account_number"),
        ("partner_bank_id.acc_holder_name", "partner_bank_id.holder_name"),
    ):
        for column in ("body_html", "subject"):
            openupgrade.logged_query(
                env.cr,
                f"""
                UPDATE mail_template
                   SET {column} = replace({column}::text, %(old)s, %(new)s)::jsonb
                 WHERE {column}::text LIKE %(like)s
                """,
                {"old": old, "new": new, "like": f"%{old}%"},
            )


def _account_report_line_foldability(env):
    """account.report.line.foldable (boolean) -> foldability (stored compute,
    readonly=False). Like odoo/upgrade_code/19.3-00-account-report-foldable.py
    for the data files: an explicit foldable=True that the compute does not
    give becomes 'foldable'. Lines of data files are updated by their module,
    only the lines created by users are converted here."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "account_report_line", "foldable"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE account_report_line arl
           SET foldability = 'foldable'
         WHERE arl.foldable
           AND arl.foldability IS DISTINCT FROM 'foldable'
           AND NOT EXISTS (
               SELECT 1 FROM ir_model_data imd
                WHERE imd.model = 'account.report.line' AND imd.res_id = arl.id)
        """,
    )
    env["account.report.line"].invalidate_model(["foldability"])


def _recompute_stored_fields(env):
    """Stored computed fields whose dependencies were written in SQL by the
    OpenUpgrade 19.0 scripts (OCA/OpenUpgrade#5980: is_domestic after the
    conversion of the fiscal position taxes; can_be_proposed after the SQL
    update of trigger) or by the pre-migration of this module."""
    taxes = env["account.tax"].with_context(active_test=False).search([])
    env.add_to_compute(taxes._fields["is_domestic"], taxes)
    taxes._recompute_recordset(["is_domestic"])
    models = env["account.reconcile.model"].with_context(active_test=False).search([])
    env.add_to_compute(models._fields["can_be_proposed"], models)
    models._recompute_recordset(["can_be_proposed"])


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "account", "20.0.1.5/noupdate_changes.xml")
    _convert_account_groups(env)
    # account.group does not exist anymore: Odoo never cleans the XML-IDs of
    # removed models (the template ones, not converted above)
    openupgrade.delete_xmlids_of_removed_models(env.cr, ["account.group"])
    _company_defaults_to_ir_default(env)
    _account_journal_group(env)
    _mail_template_partner_bank_fields(env)
    _account_report_line_foldability(env)
    _recompute_stored_fields(env)
