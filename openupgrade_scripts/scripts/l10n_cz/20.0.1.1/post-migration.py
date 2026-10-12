# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # data/res_country_data.xml sets enforce_cities and address_view_id
    # (l10n_cz.cz_partner_address_form) on base.cz. The file is not noupdate,
    # but the XML-ID base.cz is (base loads the countries as noupdate), so a
    # module update skips the record. Load it in init mode, as a fresh install
    # does.
    openupgrade.load_data(env, "l10n_cz", "data/res_country_data.xml")
