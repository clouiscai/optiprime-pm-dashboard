import asyncio
import io
import unittest
from datetime import date

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes import (
    amount_in_sgd,
    create_budget_log,
    create_invoice_purchase,
    delete_invoice,
    delete_invoice_file,
    replace_invoice_file,
    update_budget_log,
    update_invoice,
    upload_invoice,
)
from database.seed import assign_existing_unscoped_records
from database.session import Base
from models.entities import BOMItem, BudgetLog, BudgetLogReference, Invoice, Project, Team
from models.schemas import BudgetLogCreate, BudgetLogUpdate, InvoicePurchaseCreate, InvoiceUpdate
from services.calculations import project_dashboard


class FinanceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        project = Project(name="Multi-currency test", budget=1000)
        self.db.add(project)
        self.db.flush()
        uav = Team(project_id=project.id, code="UAV", name="UAV Team", domain="Aerial", budget=300)
        self.db.add(uav)
        self.db.commit()
        self.project_id = project.id
        self.uav_id = uav.id

    def tearDown(self):
        self.db.close()

    def test_conversion_rounds_to_sgd_cents(self):
        self.assertEqual(amount_in_sgd(248.37, 1.3526), 335.95)

    def test_foreign_currency_expense_preserves_original_values(self):
        payload = BudgetLogCreate(
            project_id=self.project_id,
            category="Freight",
            currency="usd",
            original_amount=248.37,
            exchange_rate_to_sgd=1.3526,
            amount=0,
            date=date(2026, 7, 3),
        )
        log = asyncio.run(create_budget_log(payload, "test-token", self.db))
        self.assertEqual(log.currency, "USD")
        self.assertEqual(log.original_amount, 248.37)
        self.assertEqual(log.exchange_rate_to_sgd, 1.3526)
        self.assertEqual(log.amount, 335.95)

    def test_invoice_number_is_optional(self):
        invoices = []
        for description in ("First receipt", "Second receipt"):
            upload = UploadFile(filename="invoice.pdf", file=io.BytesIO(b"%PDF-1.4\n%%EOF"))
            upload.headers = {"content-type": "application/pdf"}
            invoices.append(
                asyncio.run(
                    upload_invoice(
                        "test-token",
                        project_id=self.project_id,
                        vendor="Local Supplier",
                        invoice_number="",
                        sponsored_by="",
                        description=description,
                        invoice_date=date(2026, 7, 4),
                        currency="SGD",
                        exchange_rate_to_sgd=1,
                        category="",
                        original_amount=0,
                        file=upload,
                        db=self.db,
                    )
                )
            )

        self.assertEqual([invoice.invoice_number for invoice in invoices], ["", ""])
        asyncio.run(update_invoice(invoices[0].id, InvoiceUpdate(invoice_number=""), "test-token", self.db))
        self.db.refresh(invoices[0])
        self.assertEqual(invoices[0].invoice_number, "")

    def test_sponsored_materials_remain_in_bom_estimate(self):
        self.db.add_all(
            [
                BOMItem(project_id=self.project_id, team_id=self.uav_id, name="Purchased frame", quantity=2, unit_cost=50),
                BOMItem(project_id=self.project_id, team_id=self.uav_id, name="Sponsored sensor", quantity=1, unit_cost=125, sponsored_by="Partner"),
                BOMItem(project_id=self.project_id, team_id=None, name="Shared sponsored filament", quantity=3, unit_cost=30, sponsored_by="Partner"),
            ]
        )
        self.db.commit()

        master = project_dashboard(self.db, self.db.get(Project, self.project_id))
        team = project_dashboard(self.db, self.db.get(Project, self.project_id), self.uav_id)

        self.assertEqual(master["expected_spend"], 315)
        self.assertEqual(team["expected_spend"], 315)
        self.assertEqual(master["team_summaries"][0]["expected_spend"], 315)

    def test_invoice_groups_multiple_purchases_and_deletes_them(self):
        standalone = BudgetLogCreate(
            project_id=self.project_id,
            category="Legacy standalone expense",
            currency="SGD",
            original_amount=50,
            exchange_rate_to_sgd=1,
            amount=50,
            date=date(2026, 7, 3),
        )
        asyncio.run(create_budget_log(standalone, "test-token", self.db))
        upload = UploadFile(filename="invoice.pdf", file=io.BytesIO(b"%PDF-1.4\n%%EOF"))
        upload.headers = {"content-type": "application/pdf"}
        invoice = asyncio.run(
            upload_invoice(
                "test-token",
                project_id=self.project_id,
                vendor="Blue Robotics",
                invoice_number="BR-1042",
                sponsored_by="Ocean Foundation",
                description="USD component order",
                invoice_date=date(2026, 7, 3),
                currency="USD",
                exchange_rate_to_sgd=1.35,
                category="",
                original_amount=0,
                file=upload,
                db=self.db,
            )
        )
        first = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Components", quantity=2, original_amount=100, notes="Thruster", team_ids=[self.uav_id]),
                "test-token",
                self.db,
            )
        )
        second = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Discount", original_amount=20, notes="Supplier discount", referenced_item_ids=[first.id]),
                "test-token",
                self.db,
            )
        )
        self.db.refresh(invoice)
        self.assertEqual(invoice.vendor, "Blue Robotics")
        self.assertEqual(invoice.invoice_number, "BR-1042")
        self.assertIsNone(invoice.team_id)
        self.assertEqual(invoice.sponsored_by, "Ocean Foundation")
        self.assertEqual(invoice.original_amount, 180)
        self.assertEqual(invoice.amount_sgd, 243)
        self.assertEqual(first.invoice_id, invoice.id)
        self.assertEqual(second.invoice_id, invoice.id)
        self.assertEqual(first.sponsored_by, "Ocean Foundation")
        self.assertEqual(second.sponsored_by, "Ocean Foundation")
        self.assertEqual(first.team_ids, [self.uav_id])
        self.assertEqual(second.team_ids, [])
        self.assertEqual(second.original_amount, -20)
        self.assertEqual(second.referenced_item_ids, [first.id])
        self.assertEqual(first.quantity, 2)
        self.assertEqual(first.amount, 270)
        self.assertEqual(self.db.query(BudgetLog).filter(BudgetLog.invoice_id == invoice.id).count(), 2)
        project = self.db.get(Project, self.project_id)
        self.assertEqual(project_dashboard(self.db, project)["actual_spend"], 0)
        asyncio.run(update_invoice(invoice.id, InvoiceUpdate(sponsored_by=""), "test-token", self.db))
        self.db.refresh(first)
        self.db.refresh(second)
        self.assertEqual(first.sponsored_by, "")
        self.assertEqual(second.sponsored_by, "")
        self.assertEqual(project_dashboard(self.db, project)["actual_spend"], 243)
        team_dashboard = project_dashboard(self.db, project, self.uav_id)
        self.assertEqual(team_dashboard["actual_spend"], 243)
        self.assertEqual(team_dashboard["remaining_budget"], 57)
        master_dashboard = project_dashboard(self.db, project)
        self.assertEqual(master_dashboard["unallocated_budget"], 700)
        self.assertEqual(master_dashboard["unallocated_actual_spend"], 0)

        asyncio.run(update_budget_log(first.id, BudgetLogUpdate(quantity=3), "test-token", self.db))
        self.db.refresh(invoice)
        self.db.refresh(first)
        self.assertEqual(first.quantity, 3)
        self.assertEqual(first.amount, 405)
        self.assertEqual(invoice.original_amount, 280)
        self.assertEqual(invoice.amount_sgd, 378)
        self.assertEqual(project_dashboard(self.db, project, self.uav_id)["actual_spend"], 378)

        asyncio.run(update_budget_log(first.id, BudgetLogUpdate(team_ids=[]), "test-token", self.db))
        self.db.refresh(invoice)
        self.db.refresh(first)
        self.db.refresh(second)
        self.assertEqual(first.team_ids, [])
        self.assertIsNone(first.team_id)
        self.assertIsNone(second.team_id)
        self.assertEqual(project_dashboard(self.db, project, self.uav_id)["actual_spend"], 0)
        master_dashboard = project_dashboard(self.db, project)
        self.assertEqual(master_dashboard["unallocated_actual_spend"], 378)
        self.assertEqual(master_dashboard["unallocated_remaining"], 322)
        replacement = UploadFile(filename="corrected.pdf", file=io.BytesIO(b"%PDF-1.7\n%%EOF"))
        replacement.headers = {"content-type": "application/pdf"}
        asyncio.run(replace_invoice_file(invoice.id, "test-token", replacement, self.db))
        self.db.refresh(invoice)
        self.assertTrue(invoice.has_pdf)
        self.assertEqual(invoice.original_filename, "corrected.pdf")
        asyncio.run(delete_invoice_file(invoice.id, "test-token", self.db))
        self.db.refresh(invoice)
        self.assertFalse(invoice.has_pdf)
        self.assertEqual(invoice.original_filename, "")
        self.assertEqual(self.db.query(Invoice).count(), 1)
        asyncio.run(delete_invoice(invoice.id, "test-token", self.db))
        self.assertEqual(self.db.query(Invoice).count(), 0)
        self.assertEqual(self.db.query(BudgetLog).count(), 1)

    def test_invoice_lines_split_teams_and_adjustments_follow_items(self):
        self.db.add_all(
            [
                Team(project_id=self.project_id, code="USV", name="USV Team", domain="Surface", budget=300),
                Team(project_id=self.project_id, code="UUV", name="UUV Team", domain="Underwater", budget=300),
            ]
        )
        self.db.commit()
        teams = {team.code: team for team in self.db.query(Team).filter(Team.project_id == self.project_id).all()}

        upload = UploadFile(filename="shared-order.pdf", file=io.BytesIO(b"%PDF-1.4\n%%EOF"))
        upload.headers = {"content-type": "application/pdf"}
        invoice = asyncio.run(
            upload_invoice(
                "test-token",
                project_id=self.project_id,
                vendor="Shared Supplier",
                invoice_number="SHARED-1",
                sponsored_by="",
                description="Mixed team order",
                invoice_date=date(2026, 7, 5),
                currency="SGD",
                exchange_rate_to_sgd=1,
                category="",
                original_amount=0,
                file=upload,
                db=self.db,
            )
        )
        uav_item = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Item", original_amount=100, notes="UAV part", team_ids=[teams["UAV"].id]),
                "test-token",
                self.db,
            )
        )
        shared_item = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Item", original_amount=300, notes="Shared electronics", team_ids=[teams["UAV"].id, teams["USV"].id]),
                "test-token",
                self.db,
            )
        )
        asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Shipping", original_amount=90, notes="Freight", team_ids=[team.id for team in teams.values()]),
                "test-token",
                self.db,
            )
        )
        tax = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(
                    category="Tax",
                    original_amount=0,
                    adjustment_mode="percentage",
                    adjustment_rate=10,
                    notes="GST",
                    referenced_item_ids=[uav_item.id, shared_item.id],
                ),
                "test-token",
                self.db,
            )
        )
        discount = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(
                    category="Discount",
                    original_amount=0,
                    adjustment_mode="percentage",
                    adjustment_rate=5,
                    notes="Bundle discount",
                    referenced_item_ids=[uav_item.id, shared_item.id],
                ),
                "test-token",
                self.db,
            )
        )

        self.assertEqual(tax.original_amount, 40)
        self.assertEqual(tax.amount, 40)
        self.assertEqual(discount.original_amount, -20)
        self.assertEqual(discount.amount, -20)

        project = self.db.get(Project, self.project_id)
        dashboards = {
            code: project_dashboard(self.db, project, team.id)
            for code, team in teams.items()
        }
        self.assertEqual(project_dashboard(self.db, project)["actual_spend"], 510)
        self.assertEqual(dashboards["UAV"]["actual_spend"], 292.5)
        self.assertEqual(dashboards["USV"]["actual_spend"], 187.5)
        self.assertEqual(dashboards["UUV"]["actual_spend"], 30)

        asyncio.run(
            update_budget_log(
                shared_item.id,
                BudgetLogUpdate(
                    original_amount=500,
                    inventory_category="Equipment",
                    inventory_available=False,
                    inventory_note="Awaiting repair",
                ),
                "test-token",
                self.db,
            )
        )
        self.db.refresh(tax)
        self.db.refresh(discount)
        self.db.refresh(shared_item)
        self.assertEqual(tax.original_amount, 60)
        self.assertEqual(discount.original_amount, -30)
        self.assertEqual(shared_item.inventory_category, "Equipment")
        self.assertFalse(shared_item.inventory_available)
        self.assertEqual(shared_item.inventory_note, "Awaiting repair")

    def test_dashboard_groups_spending_by_type_and_inventory_category(self):
        invoice = Invoice(
            project_id=self.project_id,
            vendor="Mixed Supplier",
            description="Dashboard classification",
            original_filename="",
            stored_filename="",
        )
        self.db.add(invoice)
        self.db.flush()
        hardware = BudgetLog(
            project_id=self.project_id,
            invoice_id=invoice.id,
            category="Item",
            inventory_category="Assets",
            amount=100,
            date=date(2026, 7, 7),
        )
        service = BudgetLog(
            project_id=self.project_id,
            invoice_id=invoice.id,
            category="Service",
            amount=50,
            date=date(2026, 7, 7),
        )
        enablement = BudgetLog(
            project_id=self.project_id,
            invoice_id=invoice.id,
            category="Enablement",
            amount=25,
            date=date(2026, 7, 7),
        )
        self.db.add_all([hardware, service, enablement])
        self.db.flush()
        tax = BudgetLog(
            project_id=self.project_id,
            invoice_id=invoice.id,
            category="Tax",
            amount=10,
            date=date(2026, 7, 7),
        )
        self.db.add(tax)
        self.db.flush()
        tax.reference_links = [BudgetLogReference(target_log_id=hardware.id)]
        self.db.commit()

        dashboard = project_dashboard(self.db, self.db.get(Project, self.project_id))
        summaries = {summary["type"]: summary for summary in dashboard["spending_type_summaries"]}

        self.assertEqual(dashboard["actual_spend"], 185)
        self.assertEqual(summaries["Items"]["amount"], 110)
        self.assertEqual(summaries["Items"]["categories"], [{"category": "Hardware", "amount": 110}])
        self.assertEqual(summaries["Services"]["amount"], 50)
        self.assertEqual(summaries["Team Enablement"]["amount"], 25)

    def test_inventory_tracks_partial_out_of_service_quantity(self):
        item = BudgetLog(
            project_id=self.project_id,
            category="Item",
            quantity=5,
            amount=50,
            date=date(2026, 7, 8),
        )
        self.db.add(item)
        self.db.commit()

        asyncio.run(
            update_budget_log(
                item.id,
                BudgetLogUpdate(inventory_unavailable_quantity=2),
                "test-token",
                self.db,
            )
        )
        self.assertEqual(item.inventory_unavailable_quantity, 2)
        self.assertTrue(item.inventory_available)

        asyncio.run(
            update_budget_log(
                item.id,
                BudgetLogUpdate(inventory_unavailable_quantity=5),
                "test-token",
                self.db,
            )
        )
        self.assertFalse(item.inventory_available)

        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                update_budget_log(
                    item.id,
                    BudgetLogUpdate(inventory_unavailable_quantity=6),
                    "test-token",
                    self.db,
                )
            )
        self.assertEqual(context.exception.status_code, 400)

    def test_seed_does_not_reassign_general_invoice_lines(self):
        invoice = Invoice(
            project_id=self.project_id,
            description="General order",
            original_filename="",
            stored_filename="",
        )
        self.db.add(invoice)
        self.db.flush()
        invoice_line = BudgetLog(
            project_id=self.project_id,
            invoice_id=invoice.id,
            category="Item",
            amount=25,
            date=date(2026, 7, 6),
        )
        legacy_log = BudgetLog(
            project_id=self.project_id,
            category="Legacy expense",
            amount=10,
            date=date(2026, 7, 6),
        )
        self.db.add_all([invoice_line, legacy_log])
        self.db.commit()

        project = self.db.get(Project, self.project_id)
        teams = {"UAV": self.db.get(Team, self.uav_id), "USV": self.db.get(Team, self.uav_id), "UUV": self.db.get(Team, self.uav_id)}
        assign_existing_unscoped_records(self.db, project, teams)

        self.assertIsNone(invoice_line.team_id)
        self.assertEqual(legacy_log.team_id, self.uav_id)


if __name__ == "__main__":
    unittest.main()
