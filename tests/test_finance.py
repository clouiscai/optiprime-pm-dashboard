import asyncio
import io
import unittest
from datetime import date

from fastapi import UploadFile
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
from database.session import Base
from models.entities import BOMItem, BudgetLog, Invoice, Project, Team
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
                        team_id=None,
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
                team_id=self.uav_id,
                category="",
                original_amount=0,
                file=upload,
                db=self.db,
            )
        )
        first = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Components", quantity=2, original_amount=100, notes="Thruster"),
                "test-token",
                self.db,
            )
        )
        second = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Discount", original_amount=-20, notes="Supplier discount"),
                "test-token",
                self.db,
            )
        )
        self.db.refresh(invoice)
        self.assertEqual(invoice.vendor, "Blue Robotics")
        self.assertEqual(invoice.invoice_number, "BR-1042")
        self.assertEqual(invoice.team_id, self.uav_id)
        self.assertEqual(invoice.sponsored_by, "Ocean Foundation")
        self.assertEqual(invoice.original_amount, 180)
        self.assertEqual(invoice.amount_sgd, 243)
        self.assertEqual(first.invoice_id, invoice.id)
        self.assertEqual(second.invoice_id, invoice.id)
        self.assertEqual(first.sponsored_by, "Ocean Foundation")
        self.assertEqual(second.sponsored_by, "Ocean Foundation")
        self.assertEqual(first.team_id, self.uav_id)
        self.assertEqual(second.team_id, self.uav_id)
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

        asyncio.run(update_invoice(invoice.id, InvoiceUpdate(team_id=None), "test-token", self.db))
        self.db.refresh(invoice)
        self.db.refresh(first)
        self.db.refresh(second)
        self.assertIsNone(invoice.team_id)
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


if __name__ == "__main__":
    unittest.main()
