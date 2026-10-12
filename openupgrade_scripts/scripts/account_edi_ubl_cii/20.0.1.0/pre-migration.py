# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

# Same selection keys / meaning, the new fields are stored computed fields
# (readonly=False): keep the values (they may have been set by hand) instead
# of letting the ORM recompute them for every partner.
_renamed_fields = [
    ("res.partner", "res_partner", "peppol_eas", "routing_scheme"),
    ("res.partner", "res_partner", "peppol_endpoint", "routing_endpoint"),
]


@openupgrade.migrate()
def migrate(env, version):
    if not openupgrade.column_exists(env.cr, "res_partner", "routing_scheme"):
        openupgrade.rename_fields(env, _renamed_fields)
