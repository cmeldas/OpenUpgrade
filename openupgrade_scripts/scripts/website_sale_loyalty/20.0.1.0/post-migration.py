# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# templates removed in v20
_removed_views = [
    "website_sale_loyalty.cart_discount",
    "website_sale_loyalty.reduction_coupon_code",
]


def _remove_cow_copies_of_removed_views(env):
    """The module removes the original templates at the end of the update,
    but their website specific copies (COW, same key, website_id set, no
    XML-ID) would stay and keep patching the website_sale templates with the
    v19 xpaths. Delete the copies that are identical to the original;
    deactivate the ones the user customised."""
    View = env["ir.ui.view"].with_context(active_test=False)
    for xmlid in _removed_views:
        original = env.ref(xmlid, raise_if_not_found=False)
        if not original:
            continue
        copies = View.search(
            [
                ("key", "=", original.key),
                ("website_id", "!=", False),
                ("id", "!=", original.id),
            ]
        )
        unchanged = copies.filtered(lambda v: v.arch_db == original.arch_db)
        customised = copies - unchanged
        if customised:
            _logger.warning(
                "website_sale_loyalty: deactivating customised copies %s of the "
                "removed template %s",
                customised.ids,
                xmlid,
            )
            customised.write({"active": False})
        if unchanged:
            unchanged.unlink()


@openupgrade.migrate()
def migrate(env, version):
    _remove_cow_copies_of_removed_views(env)
