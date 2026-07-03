import asyncio
import io
import unittest
from datetime import date

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes import amount_in_sgd, create_budget_log, create_invoice_purchase, delete_invoice, update_invoice, upload_invoice
from database.session import Base
from models.entities import BudgetLog, Invoice, Project
from models.schemas import BudgetLogCreate, InvoicePurchaseCreate, InvoiceUpdate
from services.calculations import project_dashboard


class FinanceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        project = Project(name="Multi-currency test", budget=1000)
        self.db.add(project)
        self.db.commit()
        self.project_id = project.id

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

    def test_invoice_groups_multiple_purchases_and_deletes_them(self):
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
                team_id=None,
                category="",
                original_amount=0,
                file=upload,
                db=self.db,
            )
        )
        first = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Components", original_amount=100, notes="Thruster"),
                "test-token",
                self.db,
            )
        )
        second = asyncio.run(
            create_invoice_purchase(
                invoice.id,
                InvoicePurchaseCreate(category="Shipping", original_amount=20, notes="Freight"),
                "test-token",
                self.db,
            )
        )
        self.db.refresh(invoice)
        self.assertEqual(invoice.vendor, "Blue Robotics")
        self.assertEqual(invoice.invoice_number, "BR-1042")
        self.assertEqual(invoice.sponsored_by, "Ocean Foundation")
        self.assertEqual(invoice.original_amount, 120)
        self.assertEqual(invoice.amount_sgd, 162)
        self.assertEqual(first.invoice_id, invoice.id)
        self.assertEqual(second.invoice_id, invoice.id)
        self.assertEqual(first.sponsored_by, "Ocean Foundation")
        self.assertEqual(second.sponsored_by, "Ocean Foundation")
        self.assertEqual(self.db.query(BudgetLog).filter(BudgetLog.invoice_id == invoice.id).count(), 2)
        project = self.db.get(Project, self.project_id)
        self.assertEqual(project_dashboard(self.db, project)["actual_spend"], 0)
        asyncio.run(update_invoice(invoice.id, InvoiceUpdate(sponsored_by=""), "test-token", self.db))
        self.db.refresh(first)
        self.db.refresh(second)
        self.assertEqual(first.sponsored_by, "")
        self.assertEqual(second.sponsored_by, "")
        self.assertEqual(project_dashboard(self.db, project)["actual_spend"], 162)
        self.assertEqual(self.db.query(Invoice).count(), 1)
        asyncio.run(delete_invoice(invoice.id, "test-token", self.db))
        self.assertEqual(self.db.query(Invoice).count(), 0)
        self.assertEqual(self.db.query(BudgetLog).count(), 0)


if __name__ == "__main__":
    unittest.main()
