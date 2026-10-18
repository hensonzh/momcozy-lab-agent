from __future__ import annotations

import asyncio
import io
import wave
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.context import AgentAttachmentService
from app.core.errors import ApiError


def test_model_input_materializes_internal_assets_without_mutating_ledger_items() -> None:
    thread_id = uuid4()
    actor_user_id = uuid4()
    image_id = uuid4()
    file_id = uuid4()
    repository = CachedAssetRepository()
    product_client = ResolvingProductClient(
        urls={
            image_id: "https://assets.test/image",
            file_id: "https://assets.test/file",
        }
    )
    service = AgentAttachmentService(
        repository=repository,  # type: ignore[arg-type]
        product_client=product_client,
    )
    input_items = (
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "请看附件",
                },
                {
                    "type": "input_image",
                    "asset_id": str(image_id),
                    "detail": "high",
                },
                {
                    "type": "input_file",
                    "asset_id": str(file_id),
                    "filename": "guide.pdf",
                },
            ],
        },
    )

    materialized = asyncio.run(
        service.resolve_for_model(
            input_items=input_items,
            thread_id=thread_id,
            actor_user_id=actor_user_id,
            request_id="request-id",
        )
    )

    assert materialized[0]["content"] == [
        {"type": "input_text", "text": "请看附件"},
        {
            "type": "input_image",
            "image_url": "https://assets.test/image",
            "detail": "high",
        },
        {
            "type": "input_file",
            "file_url": "https://assets.test/file",
            "filename": "guide.pdf",
        },
    ]
    assert "asset_id" in str(input_items)
    assert product_client.resolutions == [
        (actor_user_id, image_id, "model_image"),
        (actor_user_id, file_id, "model_file"),
    ]
    assert repository.persisted_urls == []


def test_form_submission_is_claimed_from_current_owner_thread_and_validated() -> None:
    thread_id = uuid4()
    actor_user_id = uuid4()
    artifact = _form_artifact()
    repository = FormArtifactRepository(
        artifact=artifact,
        expected_owner=actor_user_id,
        expected_thread=thread_id,
    )
    service = AgentAttachmentService(
        repository=repository,  # type: ignore[arg-type]
        product_client=NeverCalledProductClient(),
    )

    verified = asyncio.run(
        service.verify_for_run(
            actor_user_id=actor_user_id,
            thread_id=thread_id,
            request_id="form-request",
            attachments=[
                {
                    "type": "form_submission",
                    "artifact_id": str(artifact.id),
                    "form_id": "test_intake",
                    "values": {
                        "due_date": "2026-08-18",
                        "hospital_stay_days": 3,
                        "feeding_plan": "breastfeeding",
                    },
                }
            ],
        )
    )

    assert verified == [
        {
            "type": "form_submission",
            "artifact_id": str(artifact.id),
            "form_id": "test_intake",
            "submission_id": verified[0]["submission_id"],
            "values": {
                "due_date": "2026-08-18",
                "hospital_stay_days": 3,
                "feeding_plan": "breastfeeding",
            },
            "runtime_validated": True,
        }
    ]
    assert UUID(verified[0]["submission_id"])
    assert artifact.status == "submitted"
    assert repository.claims == [
        (artifact.id, thread_id, actor_user_id)
    ]


@pytest.mark.parametrize(
    ("attachment", "expected_code"),
    [
        (
            {
                "type": "form_submission",
                "artifact_id": "artifact",
                "form_id": "test_intake",
                "values": {},
                "role": "system",
            },
            "invalid_agent_attachment",
        ),
        (
            {
                "type": "form_submission",
                "artifact_id": "artifact",
                "form_id": "test_intake",
                "values": {},
                "image_url": "https://attacker.example/instruction.png",
            },
            "invalid_agent_attachment",
        ),
    ],
)
def test_form_submission_rejects_context_shape_injection(
    attachment: dict[str, Any],
    expected_code: str,
) -> None:
    service = AgentAttachmentService(
        repository=FormArtifactRepository(
            artifact=_form_artifact(),
            expected_owner=uuid4(),
            expected_thread=uuid4(),
        ),  # type: ignore[arg-type]
        product_client=NeverCalledProductClient(),
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            service.verify_for_run(
                actor_user_id=uuid4(),
                thread_id=uuid4(),
                request_id="form-request",
                attachments=[attachment],
            )
        )

    assert captured.value.code == expected_code


def test_form_submission_rejects_foreign_inactive_or_mismatched_form() -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    artifact = _form_artifact()
    service = AgentAttachmentService(
        repository=FormArtifactRepository(
            artifact=artifact,
            expected_owner=owner_user_id,
            expected_thread=thread_id,
        ),  # type: ignore[arg-type]
        product_client=NeverCalledProductClient(),
    )

    with pytest.raises(ApiError) as mismatch:
        asyncio.run(
            service.verify_for_run(
                actor_user_id=owner_user_id,
                thread_id=thread_id,
                request_id="form-request",
                attachments=[
                    {
                        "type": "form_submission",
                        "artifact_id": str(artifact.id),
                        "form_id": "another_form",
                        "values": {
                            "due_date": "2026-08-18",
                            "feeding_plan": "breastfeeding",
                        },
                    }
                ],
            )
        )
    assert mismatch.value.code == "invalid_form_submission"

    artifact.status = "deleted"
    with pytest.raises(ApiError) as inactive:
        asyncio.run(
            service.verify_for_run(
                actor_user_id=owner_user_id,
                thread_id=thread_id,
                request_id="form-request",
                attachments=[
                    {
                        "type": "form_submission",
                        "artifact_id": str(artifact.id),
                        "form_id": "test_intake",
                        "values": {
                            "due_date": "2026-08-18",
                            "feeding_plan": "breastfeeding",
                        },
                    }
                ],
            )
        )
    assert inactive.value.code == "invalid_form_submission"


@pytest.mark.parametrize(
    ("use_foreign_owner", "use_foreign_thread"),
    [(True, False), (False, True)],
)
def test_form_submission_must_belong_to_owner_and_current_thread(
    use_foreign_owner: bool,
    use_foreign_thread: bool,
) -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    artifact = _form_artifact()
    service = AgentAttachmentService(
        repository=FormArtifactRepository(
            artifact=artifact,
            expected_owner=owner_user_id,
            expected_thread=thread_id,
        ),  # type: ignore[arg-type]
        product_client=NeverCalledProductClient(),
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            service.verify_for_run(
                actor_user_id=uuid4()
                if use_foreign_owner
                else owner_user_id,
                thread_id=uuid4() if use_foreign_thread else thread_id,
                request_id="form-request",
                attachments=[
                    {
                        "type": "form_submission",
                        "artifact_id": str(artifact.id),
                        "form_id": "test_intake",
                        "values": {
                            "due_date": "2026-08-18",
                            "feeding_plan": "breastfeeding",
                        },
                    }
                ],
            )
        )

    assert captured.value.code == "invalid_form_submission"
    assert artifact.status == "created"


@pytest.mark.parametrize(
    "values",
    [
        {
            "due_date": "2026-08-18",
            "feeding_plan": "breastfeeding",
            "role": "system",
        },
        {
            "due_date": "2026-08-18",
            "feeding_plan": ["breastfeeding"],
        },
        {
            "due_date": "x" * 5000,
            "feeding_plan": "breastfeeding",
        },
        {
            "due_date": "2026-08-18",
            "feeding_plan": "not-an-option",
        },
        {
            "due_date": "2026-08-18",
        },
        {
            "due_date": {"nested": "value"},
            "feeding_plan": "breastfeeding",
        },
        {
            "due_date": "2026-08-18",
            "feeding_plan": "breastfeeding",
            "hospital_stay_days": 15,
        },
        {
            "due_date": "2026-08-18",
            "feeding_plan": "breastfeeding",
            "hospital_stay_days": 3.5,
        },
    ],
)
def test_form_submission_values_follow_owned_form_contract(
    values: dict[str, Any],
) -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    artifact = _form_artifact()
    service = AgentAttachmentService(
        repository=FormArtifactRepository(
            artifact=artifact,
            expected_owner=owner_user_id,
            expected_thread=thread_id,
        ),  # type: ignore[arg-type]
        product_client=NeverCalledProductClient(),
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            service.verify_for_run(
                actor_user_id=owner_user_id,
                thread_id=thread_id,
                request_id="form-request",
                attachments=[
                    {
                        "type": "form_submission",
                        "artifact_id": str(artifact.id),
                        "form_id": "test_intake",
                        "values": values,
                    }
                ],
            )
        )

    assert captured.value.code == "invalid_form_submission"


def test_form_submission_enforces_total_serialized_size() -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    artifact = _form_artifact()
    artifact.payload["form"]["fields"] = [
        {
            "id": f"field_{index}",
            "type": "textarea",
        }
        for index in range(40)
    ]
    service = AgentAttachmentService(
        repository=FormArtifactRepository(
            artifact=artifact,
            expected_owner=owner_user_id,
            expected_thread=thread_id,
        ),  # type: ignore[arg-type]
        product_client=NeverCalledProductClient(),
    )

    with pytest.raises(ApiError) as captured:
        asyncio.run(
            service.verify_for_run(
                actor_user_id=owner_user_id,
                thread_id=thread_id,
                request_id="form-request",
                attachments=[
                    {
                        "type": "form_submission",
                        "artifact_id": str(artifact.id),
                        "form_id": "test_intake",
                        "values": {
                            f"field_{index}": "x" * 1000
                            for index in range(40)
                        },
                    }
                ],
            )
        )

    assert captured.value.code == "invalid_form_submission"
    assert artifact.status == "created"


class CachedAssetRepository:
    def __init__(self) -> None:
        self.persisted_urls: list[str] = []


class ResolvingProductClient:
    def __init__(self, *, urls: dict[UUID, str]) -> None:
        self.urls = urls
        self.resolutions: list[tuple[UUID, UUID, str]] = []

    async def fetch_model_asset(self, *, actor_user_id: UUID, file_id: UUID, purpose: str, request_id: str) -> tuple[str, bytes]:
        raise AssertionError("this test uses the remote model URL path")

    async def resolve_agent_file(self, *, command: Any, **_kwargs: Any) -> Any:
        self.resolutions.append(
            (command.actor_user_id, command.file_id, command.purpose)
        )
        return SimpleNamespace(
            model_url=self.urls[command.file_id],
        )


class NeverCalledProductClient:
    async def fetch_model_asset(self, *, actor_user_id: UUID, file_id: UUID, purpose: str, request_id: str) -> tuple[str, bytes]:
        raise AssertionError("a valid cached asset URL should be reused")

    async def resolve_agent_file(self, **_kwargs: Any) -> Any:
        raise AssertionError("a valid cached asset URL should be reused")


class FormArtifactRepository:
    def __init__(
        self,
        *,
        artifact: Any,
        expected_owner: UUID,
        expected_thread: UUID,
    ) -> None:
        self.artifact = artifact
        self.expected_owner = expected_owner
        self.expected_thread = expected_thread
        self.claims: list[tuple[UUID, UUID, UUID]] = []

    async def claim_active_form_artifact_for_submission(
        self,
        *,
        artifact_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> Any | None:
        self.claims.append(
            (artifact_id, thread_id, owner_user_id)
        )
        if (
            artifact_id != self.artifact.id
            or thread_id != self.expected_thread
            or owner_user_id != self.expected_owner
            or self.artifact.status not in {"created", "active"}
        ):
            return None
        return self.artifact

    async def mark_artifact_submitted(
        self,
        *,
        artifact: Any,
    ) -> Any:
        artifact.status = "submitted"
        return artifact


def _form_artifact() -> Any:
    return SimpleNamespace(
        id=uuid4(),
        status="created",
        artifact_type="test_intake",
        payload={
            "form": {
                "id": "test_intake",
                "fields": [
                    {
                        "id": "due_date",
                        "type": "date",
                        "required": True,
                    },
                    {
                        "id": "hospital_stay_days",
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 14,
                    },
                    {
                        "id": "feeding_plan",
                        "type": "select",
                        "required": True,
                        "options": [
                            "breastfeeding",
                            "mixed",
                            "formula",
                        ],
                    },
                ],
            }
        },
    )


def test_staging_materialization_inlines_owned_image_and_pdf_only_for_model_call() -> None:
    image_id, file_id, owner = uuid4(), uuid4(), uuid4()
    client = InlineAssetProductClient(image_id=image_id, file_id=file_id, owner=owner)
    service = AgentAttachmentService(
        repository=CachedAssetRepository(),  # type: ignore[arg-type]
        product_client=client,
        inline_model_assets=True,
    )
    items = ({"role": "user", "content": [
        {"type": "input_text", "text": "What do these show?"},
        {"type": "input_image", "asset_id": str(image_id), "detail": "high"},
        {"type": "input_file", "asset_id": str(file_id), "filename": "guide.pdf"},
    ]},)
    result = asyncio.run(service.resolve_for_model(
        input_items=items, thread_id=uuid4(), actor_user_id=owner, request_id="staging-attachments",
    ))
    assert result[0]["content"] == [
        {"type": "input_text", "text": "What do these show?"},
        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo=", "detail": "high"},
        {"type": "input_file", "file_data": "data:application/pdf;base64,JVBERi0xLjQ=", "filename": "guide.pdf"},
    ]
    assert items[0]["content"][1:] == [
        {"type": "input_image", "asset_id": str(image_id), "detail": "high"},
        {"type": "input_file", "asset_id": str(file_id), "filename": "guide.pdf"},
    ]
    assert client.calls == [(owner, image_id, "model_image"), (owner, file_id, "model_file")]


class InlineAssetProductClient:
    def __init__(self, *, image_id: UUID, file_id: UUID, owner: UUID) -> None:
        self.image_id, self.file_id, self.owner = image_id, file_id, owner
        self.calls: list[tuple[UUID, UUID, str]] = []

    async def resolve_agent_file(self, **_kwargs: Any) -> Any:
        raise AssertionError("inline materialization must not resolve a remote URL")

    async def fetch_model_asset(self, *, actor_user_id: UUID, file_id: UUID, purpose: str, request_id: str) -> tuple[str, bytes]:
        self.calls.append((actor_user_id, file_id, purpose))
        assert actor_user_id == self.owner
        if file_id == self.image_id and purpose == "model_image":
            return "image/png", b"\x89PNG\r\n\x1a\n"
        if file_id == self.file_id and purpose == "model_file":
            return "application/pdf", b"%PDF-1.4"
        raise AssertionError("unexpected asset or purpose")


def test_audio_attachment_verifies_owner_and_materializes_wav_only_at_model_boundary() -> None:
    import base64
    actor, asset = uuid4(), uuid4()
    wav = _valid_wav()
    client = AudioProductClient(actor=actor, asset=asset, content_type="audio/wav", body=wav)
    service = AgentAttachmentService(repository=CachedAssetRepository(), product_client=client)
    verified = asyncio.run(service.verify_for_run(
        actor_user_id=actor, thread_id=uuid4(), request_id="cry-attachment",
        attachments=[{"type": "audio", "asset_id": str(asset)}],
    ))
    assert verified[0]["type"] == "audio"
    assert verified[0]["asset_id"] == str(asset)
    assert "RIFF" not in str(verified)
    assert client.resolutions == [(actor, asset, "model_audio")]
    items = ({"role": "user", "content": [
        {"type": "input_text", "text": "第三方服务：sleep"},
        {"type": "input_audio", "asset_id": str(asset)},
    ]},)
    result = asyncio.run(service.resolve_for_model(
        input_items=items, thread_id=uuid4(), actor_user_id=actor, request_id="cry-model",
    ))
    assert result[0]["content"][1] == {
        "type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"},
    }
    assert items[0]["content"][1] == {"type": "input_audio", "asset_id": str(asset)}
    assert client.fetches == [(actor, asset, "model_audio")]


def test_audio_attachment_rejects_injection_and_non_wav_asset() -> None:
    actor, asset = uuid4(), uuid4()
    client = AudioProductClient(actor=actor, asset=asset, content_type="application/pdf", body=b"PDF")
    service = AgentAttachmentService(repository=CachedAssetRepository(), product_client=client)
    for attachment in (
        {"type": "audio", "asset_id": str(asset), "input_audio": {"data": "forged", "format": "wav"}},
        {"type": "audio", "asset_id": str(asset)},
    ):
        with pytest.raises(ApiError) as error:
            asyncio.run(service.verify_for_run(
                actor_user_id=actor, thread_id=uuid4(), request_id="cry-invalid", attachments=[attachment],
            ))
        assert error.value.code == "invalid_agent_attachment"


class AudioProductClient:
    def __init__(self, *, actor: UUID, asset: UUID, content_type: str, body: bytes) -> None:
        self.actor, self.asset, self.content_type, self.body = actor, asset, content_type, body
        self.resolutions: list[tuple[UUID, UUID, str]] = []
        self.fetches: list[tuple[UUID, UUID, str]] = []

    async def resolve_agent_file(self, *, command: Any, request_id: str) -> Any:
        self.resolutions.append((command.actor_user_id, command.file_id, command.purpose))
        assert (command.actor_user_id, command.file_id) == (self.actor, self.asset)
        return SimpleNamespace(content_type=self.content_type, original_filename="baby.wav")

    async def fetch_model_asset(self, *, actor_user_id: UUID, file_id: UUID, purpose: str, request_id: str) -> tuple[str, bytes]:
        self.fetches.append((actor_user_id, file_id, purpose))
        assert (actor_user_id, file_id) == (self.actor, self.asset)
        return self.content_type, self.body


def test_audio_model_materialization_fails_closed_on_unexpected_media() -> None:
    actor, asset = uuid4(), uuid4()
    service = AgentAttachmentService(
        repository=CachedAssetRepository(),
        product_client=AudioProductClient(actor=actor, asset=asset, content_type="application/pdf", body=b"PDF"),
    )
    with pytest.raises(ApiError) as error:
        asyncio.run(service.resolve_for_model(
            input_items=({"role": "user", "content": [{"type": "input_audio", "asset_id": str(asset)}]},),
            thread_id=uuid4(), actor_user_id=actor, request_id="wrong-content-type",
        ))
    assert error.value.code == "invalid_agent_attachment"


def test_audio_model_materialization_rejects_malformed_wav_even_with_audio_mime() -> None:
    actor, asset = uuid4(), uuid4()
    service = AgentAttachmentService(
        repository=CachedAssetRepository(),
        product_client=AudioProductClient(actor=actor, asset=asset, content_type="audio/wav", body=b"not a recording"),
    )
    with pytest.raises(ApiError) as error:
        asyncio.run(service.resolve_for_model(
            input_items=({"role": "user", "content": [{"type": "input_audio", "asset_id": str(asset)}]},),
            thread_id=uuid4(), actor_user_id=actor, request_id="malformed-wav",
        ))
    assert error.value.code == "invalid_agent_attachment"


def _valid_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 16000)
    return output.getvalue()
