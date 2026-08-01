"""Build and release tooling for the unifi-containers images."""

from pathlib import Path
from typing import Optional

import typer

from unifi_containers import (
    cut_release,
    extract_uos,
    make_seed,
    release,
    release_plan,
    simkeys,
    slide_tags,
    update,
)
from unifi_containers.updaters import network as network_updater
from unifi_containers.updaters import uos as uos_updater

app = typer.Typer(add_completion=False, help=__doc__, no_args_is_help=True)

PRODUCTS = sorted(release.IMAGES)


@app.command("cut-release")
def cut_release_cmd(
    product: Optional[str] = typer.Argument(None, help=f"one of {', '.join(PRODUCTS)}"),
    every: bool = typer.Option(False, "--all", help="consider every product; what CI runs"),
    rebuild: bool = typer.Option(False, "--rebuild", help="next build of the pinned version"),
    push: bool = typer.Option(False, "--push"),
):
    """Cut the release tag a push makes due: version from the pins, build from the tags."""
    if not product and not every:
        raise typer.BadParameter("name a product or pass --all")
    if product and product not in PRODUCTS:
        raise typer.BadParameter(f"unknown product {product!r}; expected one of {PRODUCTS}")
    cut = cut_release.cut(PRODUCTS if every else [product], push=push, rebuild=rebuild)
    raise typer.Exit(0 if (cut or not rebuild) else 1)


@app.command("release-plan")
def release_plan_cmd(
    tag: str = typer.Argument(..., help="the release git tag"),
    github: bool = typer.Option(False, "--github", help="emit KEY=VALUE for $GITHUB_OUTPUT"),
):
    """Turn a release tag into what to push, what to slide, and whether to publish."""
    raise typer.Exit(release_plan.report(tag, github=github))


@app.command("slide-tags")
def slide_tags_cmd(
    image: str = typer.Option(..., "--image"),
    tags: str = typer.Option(..., "--tags", help="space-separated names from release-plan"),
    base: Optional[str] = typer.Option(None, "--base"),
    sim: Optional[str] = typer.Option(None, "--sim"),
    seeded: Optional[str] = typer.Option(None, "--seeded"),
):
    """Point sliding tags at already-pushed digests."""
    raise typer.Exit(slide_tags.apply(image, tags, base=base, sim=sim, seeded=seeded))


@app.command("extract-uos")
def extract_uos_cmd(
    arch: Optional[str] = typer.Argument(None, help="amd64 or arm64; default this host"),
    pins: Optional[Path] = typer.Option(None, "--pins"),
):
    """Unpack the OCI image embedded in the UniFi OS installer and load it."""
    raise typer.Exit(extract_uos.extract(arch, pins or extract_uos.PINS))


@app.command("update")
def update_cmd(lane: str = typer.Argument(..., help="stable or uos")):
    """Run one updater lane and open an auto-merging bump PR."""
    if lane not in update.LANES:
        raise typer.BadParameter(f"unknown lane {lane!r}; expected {sorted(update.LANES)}")
    raise typer.Exit(update.run_lane(lane))


@app.command("bump-pins")
def bump_pins_cmd(
    product: str = typer.Argument(..., help=f"one of {', '.join(PRODUCTS)}"),
    write: bool = typer.Option(False, "--write", help="rewrite the pins and README"),
):
    """Rewrite the pins to the newest upstream release. Prints the version, or nothing."""
    if product == "network":
        version = network_updater.bump(write=write)
    elif product == "unifi-os":
        version = uos_updater.bump(write=write)
    else:
        raise typer.BadParameter(f"unknown product {product!r}; expected one of {PRODUCTS}")
    if version:
        typer.echo(version)


@app.command("verify-pins")
def verify_pins_cmd(repo_root: Optional[Path] = typer.Option(None, "--repo-root")):
    """Check both products' pins: sha256 format, URL layout, reachability."""
    root = repo_root or Path()
    # Both, always: `or` would stop at the first failure and report one product's
    # pins as unchecked rather than as fine.
    failures = [network_updater.verify(root), uos_updater.verify(root)]
    raise typer.Exit(max(failures))


@app.command("sim-keys")
def sim_keys_cmd(
    version: str = typer.Argument(..., help="upstream version, e.g. 10.4.57"),
    deb: Optional[Path] = typer.Argument(None, help="a local .deb; default download it"),
):
    """Print the simulation and demo keys the release's jars carry."""
    for key in simkeys.collect(version, deb):
        typer.echo(key)


@app.command("make-seed")
def make_seed_cmd(
    base_image: str = typer.Argument(...),
    out: Optional[Path] = typer.Argument(None),
):
    """Boot a base image, complete the first-run wizard, snapshot the volume."""
    raise typer.Exit(make_seed.build(base_image, out))
