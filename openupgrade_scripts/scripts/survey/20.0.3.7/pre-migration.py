# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The certification layouts are company colored or black in 20.0, the
    # former purple/blue/gold variants map to the company colored ones
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE survey_survey
        SET certification_report_layout = CASE
            WHEN left(certification_report_layout, 8) = 'classic_'
                THEN 'classic-1_company'
            ELSE 'modern_company'
        END
        WHERE certification_report_layout IN (
            'modern_purple', 'modern_blue', 'modern_gold',
            'classic_purple', 'classic_blue', 'classic_gold'
        )
        """,
    )
