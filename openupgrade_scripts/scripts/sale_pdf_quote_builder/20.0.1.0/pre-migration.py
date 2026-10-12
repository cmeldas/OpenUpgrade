# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The selection value 'inside' ("Inside quote pdf") of
    # product.document.attached_on_sale is gone: in v20 every binary PDF
    # document of the product can be merged into the quote, whatever this
    # field says. The documents were not shared on the portal in v19, which is
    # 'hidden' ("Never") in v20 (and what the v19 ondelete 'set default' did).
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE product_document
        SET attached_on_sale = 'hidden'
        WHERE attached_on_sale = 'inside'
        """,
    )
