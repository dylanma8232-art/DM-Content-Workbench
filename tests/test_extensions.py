import io
import json
import os
import shutil
import wave
from unittest import skipUnless
from unittest.mock import patch

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from test_workbench import WorkbenchFixture


class ExtensionTests(WorkbenchFixture):
    def test_scan_pdf_passes_text_arguments_to_renderer(self):
        pages = [{"page": 1, "body": ""}]
        with patch("extraction.shutil.which", side_effect=lambda name: name), patch("extraction.subprocess.run") as run:
            from extraction import scan_pdf

            run.return_value.stdout = "Scanned text"
            scan_pdf(b"fixture", pages)
            args = run.call_args_list[0].args[0]
            self.assertTrue(all(isinstance(value, str) for value in args))
            self.assertEqual(pages[0]["body"], "Scanned text")

    def test_api_pagination_and_bearer(self):
        from connections import json_api

        first = (b'{"items":[{"id":1,"body":"A"}],"next":"/api?p=2"}', "utf-8")
        second = (b'{"items":[{"id":2,"body":"B"}]}', "utf-8")
        with (
            patch.dict(os.environ, {"DM_API_TOKEN_TEST": "fixture"}),
            patch("connections.fetch_source", side_effect=[first, second]) as fetch,
        ):
            rows = json_api(
                "https://example.org/api",
                "title",
                "body",
                "items",
                "id",
                "next",
                "DM_API_TOKEN_TEST",
            )
            self.assertEqual([row["key"] for row in rows], ["1", "2"])
            self.assertEqual(
                fetch.call_args.args,
                ("https://example.org/api?p=2", {"Authorization": "Bearer fixture"}),
            )
        with patch(
            "connections.fetch_source",
            return_value=(b'{"items":[],"next":"https://other.example/"}', "utf-8"),
        ) as fetch:
            from fastapi import HTTPException

            with self.assertRaises(HTTPException):
                json_api(
                    "https://example.org/api", "title", "body", "items", "id", "next"
                )
            self.assertEqual(fetch.call_count, 1)

    def test_webpage_and_api_ingestion(self):
        web = b"<html><head><title>Brief</title></head><body><nav>Menu</nav><main>Shared material</main><script>ignored</script></body></html>"
        api = json.dumps(
            {
                "data": {
                    "items": [
                        {"ref": 17, "heading": "Brief", "article": "API material"}
                    ]
                }
            }
        ).encode()
        forms = [
            ({"kind": "web"}, web, "Shared material"),
            (
                {
                    "kind": "api",
                    "title_field": "heading",
                    "body_field": "article",
                    "records_path": "data.items",
                    "id_field": "ref",
                },
                api,
                "API material",
            ),
        ]
        for params, content, expected in forms:
            form = dict(
                name=params["kind"], address="https://example.org/feed", **params
            )
            with patch("connections.fetch_source", return_value=(content, "utf-8")):
                preview = self.client.post("/api/sources/preview", json=form).json()
                self.assertEqual(preview["sample"][0]["body"], expected)
                source = self.client.post("/api/sources", json=form).json()["id"]
                path = f"/api/sources/{source}/refresh"
                first = self.client.post(path).json()
                self.assertEqual(
                    first["run"]["payload"]["materials"][0]["title"], "Brief"
                )
                self.assertIsNone(self.client.post(path).json()["run"])

    def test_pdf_to_material(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=200)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    }
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 100 Td (Reading room feedback) Tj ET")
        page[NameObject("/Contents")] = stream
        file = io.BytesIO()
        writer.write(file)
        result = self.client.post(
            "/api/import-extract",
            files={"file": ("notes.pdf", file.getvalue(), "application/pdf")},
        )
        result.raise_for_status()
        self.assertEqual(json.loads(result.json()["content"])[0]["page"], 1)
        self.assertIn("Reading room feedback", result.json()["content"])
        self.assertEqual(
            self.client.post(
                "/api/import-extract", files={"file": ("bad.pdf", b"not a PDF")}
            ).status_code,
            422,
        )

    @skipUnless(shutil.which("ffmpeg"), "FFmpeg optional for local audio/video")
    def test_audio_conversion_and_transcription_contract(self):
        file = io.BytesIO()
        with wave.open(file, "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00" * 3200)

        def transcribe(url, files, data, **kwargs):
            self.assertEqual(url, "https://example.org/v1/audio/transcriptions")
            self.assertGreater(len(files["file"][1].read()), 0)
            self.assertEqual(data["model"], "test-transcriber")
            return httpx.Response(
                200, request=httpx.Request("POST", url), json={"text": "转写所得材料"}
            )

        with (
            patch.dict(
                os.environ,
                {
                    "DM_ASR_BASE_URL": "https://example.org/v1",
                    "DM_ASR_MODEL": "test-transcriber",
                },
            ),
            patch("extraction.httpx.post", side_effect=transcribe),
        ):
            response = self.client.post(
                "/api/import-extract",
                files={"file": ("voice.wav", file.getvalue(), "audio/wav")},
            )
        self.assertIn("转写所得材料", response.json()["content"])

    def test_cross_source_recommendation_and_review_version(self):
        self.client.put(
            "/api/projects/1", json={"topics": "开放时间"}
        ).raise_for_status()
        self.form["content"] = (
            "名称,正文\n甲,周末来访。希望延长开放时间。\n乙,喜欢阅读。希望公布开放时间。"
        )
        path = f"/api/runs/{self.create()}"
        self.client.post(path + "/advance", json={"stage": 1}).raise_for_status()
        data = self.client.post(path + "/advance", json={"stage": 2}).json()["payload"]
        choice = data["recommendations"][0]
        self.assertEqual(choice["ids"], [1, 2])
        self.client.post(
            path + "/advance", json={"stage": 3, "candidate_key": choice["key"]}
        ).raise_for_status()
        self.client.post(path + "/advance", json={"stage": 4}).raise_for_status()
        draft = self.client.get(path + "/export").text
        self.assertIn("希望延长开放时间", draft)
        self.assertIn("希望公布开放时间", draft)
        self.client.post(
            path + "/review", json={"version": 1, "decision": "通过"}
        ).raise_for_status()
        self.client.put(
            path + "/draft", json={"version": 1, "content": "修订后"}
        ).raise_for_status()
        self.assertEqual(
            self.client.post(
                path + "/review", json={"version": 1, "decision": "通过"}
            ).status_code,
            409,
        )
