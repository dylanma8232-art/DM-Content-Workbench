from unittest.mock import patch

import httpx
from test_workbench import WorkbenchFixture


class ConnectorTests(WorkbenchFixture):
    def test_preview_then_atomic_import(self):
        path = "/api/projects/1/connectors/reports"
        config = {"url": "https://example.org/reports", "records_path": "data.items"}
        self.client.put(path, json=config).raise_for_status()
        rows = [
            {
                "platform": "书评",
                "account": "编辑部",
                "day": "2026-01-01",
                "followers": 3,
                "views": 5,
                "engagements": 1,
                "published": 1,
            }
        ]
        with patch("connections.json_records", return_value=rows):
            preview = self.client.post(path + "/preview").json()
            self.assertEqual(preview["count"], 1)
            payload = {"preview_id": preview["preview_id"]}
            dest = "/api/projects/1/account-daily?day=2026-01-01"
            self.assertEqual(self.client.get(dest).json()["accounts"], [])
            rows[0]["views"] = 6
            self.assertEqual(
                self.client.post(path + "/import", json=payload).status_code, 409
            )
            rows[0]["views"] = 5
            self.client.post(path + "/import", json=payload).raise_for_status()
            self.assertEqual(self.client.get(dest).json()["accounts"][0]["views"], 5)
            rows.append(rows[0])
            self.assertEqual(self.client.post(path + "/preview").status_code, 422)

    def test_only_approved_version_and_send_once(self):
        run = self.create()
        base = "/api/runs/" + str(run)
        for stage in range(1, 5):
            self.client.post(
                base + "/advance", json={"stage": stage, "topic_id": 1}
            ).raise_for_status()
        path = base + "/webhook/1/"
        self.client.put(
            "/api/projects/1/connectors/drafts",
            json={"url": "https://example.org/publish"},
        ).raise_for_status()
        self.assertEqual(self.client.post(path + "preview").status_code, 409)
        self.client.post(
            base + "/review", json={"version": 1, "decision": "通过", "note": ""}
        ).raise_for_status()
        response = self.client.post(path + "preview").json()
        self.assertEqual(response["payload"]["event"], "draft.approved")
        payload = {"preview_id": response["preview_id"]}
        with (
            patch("connections.public_endpoint"),
            patch("integrations.httpx.stream") as delivery,
        ):
            delivery.return_value.__enter__.return_value = httpx.Response(
                200, request=httpx.Request("POST", response["target"])
            )
            self.client.post(path + "send", json=payload).raise_for_status()
            self.client.post(path + "send", json=payload).raise_for_status()
            delivery.assert_called_once()
            self.assertEqual(
                delivery.call_args.kwargs["headers"]["Idempotency-Key"],
                payload["preview_id"],
            )
