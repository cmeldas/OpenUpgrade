# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

# document layouts removed in 20.0
_removed_layouts = (
    "web.external_layout_bold",
    "web.external_layout_boxed",
    "web.external_layout_striped",
)


@openupgrade.migrate()
def migrate(env, version):
    """Companies using a removed document layout get the standard one
    (otherwise the view removal would leave them without layout)."""
    standard = env.ref("web.external_layout_standard", raise_if_not_found=False)
    removed_ids = [
        view.id
        for view in (
            env.ref(xmlid, raise_if_not_found=False) for xmlid in _removed_layouts
        )
        if view
    ]
    if not standard or not removed_ids:
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE res_company
        SET external_report_layout_id = %s
        WHERE external_report_layout_id IN %s
        """,
        (standard.id, tuple(removed_ids)),
        skip_no_result=True,
    )
