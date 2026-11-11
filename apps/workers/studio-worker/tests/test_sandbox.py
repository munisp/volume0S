"""The workflow must pass Temporal's sandbox validation (the worker refuses to start otherwise), and both graphs
must render for a valid pack while refusing photos from outside the business's uploads."""
import json

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
from temporalio.workflow import _Definition

import studio_main
from studio_main import StudioInput, StudioWorkflow, render_workflow


async def test_workflow_loads_in_the_temporal_sandbox():
    SandboxedWorkflowRunner().prepare_workflow(_Definition.must_from_class(StudioWorkflow))


def pack(**overrides):
    base = dict(tenant_id="acme", pack_id="p1", product_id="tote", tier="growth", source_image="acme/uploads/tote.png",
                checkpoint="v1-5-pruned-emaonly.safetensors", materials="Adire cotton", dimensions="42 cm", price_text="",
                style='on a wooden table", "evil": "x')
    base.update(overrides)
    return StudioInput(**base)


@pytest.mark.parametrize("version", ["shopfront-img2img-v1", "shopfront-img2img-lcm-v1"])
def test_both_graphs_render_and_carry_the_style_safely(monkeypatch, version):
    monkeypatch.setattr(studio_main, "WORKFLOW_VERSION", version)
    graph = render_workflow(pack(), "volumeos/acme/p1-abc.png")
    assert graph["10"]["inputs"]["image"] == "volumeos/acme/p1-abc.png"
    assert 'on a wooden table", "evil": "x' in graph["6"]["inputs"]["text"]  # text stays text: no graph injection
    assert graph["12"]["inputs"]["amount"] == 7
    assert "evil" not in graph


def test_a_photo_from_another_business_is_refused():
    with pytest.raises(ApplicationError):
        render_workflow(pack(source_image="other/uploads/tote.png"))
    with pytest.raises(ApplicationError):
        render_workflow(pack(source_image="acme/packs/p0/qa/asset-01.png"))


def test_cpu_graph_matches_comfyui_v0_45_node_inputs(monkeypatch):
    """Inputs ComfyUI v0.45 requires (checked against the live /object_info on 2026-11-11)."""
    monkeypatch.setattr(studio_main, "WORKFLOW_VERSION", "shopfront-img2img-lcm-v1")
    graph = render_workflow(pack(), "x.png")
    assert graph["13"]["inputs"]["resolution_steps"] == 8
    assert graph["3"]["inputs"]["sampler_name"] == "lcm" and graph["5"]["inputs"]["lora_name"] == "lcm-lora-sdv1-5.safetensors"


def test_delivered_images_fill_their_frame_even_from_small_sources():
    import io
    from PIL import Image
    from studio_main import delivery_variants
    out = io.BytesIO(); Image.new("RGB", (560, 560), (20, 40, 120)).save(out, "PNG")
    for name, size in (("whatsapp", 1080), ("jumia", 1000)):
        image = Image.open(io.BytesIO(delivery_variants(out.getvalue())[name]))
        assert image.size == (size, size)
        assert image.getpixel((5, 5))[2] > 90 and image.getpixel((5, 5))[0] < 60  # the product fills it: no white border
    wide = io.BytesIO(); Image.new("RGB", (800, 400), (20, 40, 120)).save(wide, "PNG")
    jumia = Image.open(io.BytesIO(delivery_variants(wide.getvalue())["jumia"]))
    assert jumia.getpixel((500, 5)) == (255, 255, 255) and jumia.getpixel((500, 500))[2] > 90  # wide: white bars only top/bottom
