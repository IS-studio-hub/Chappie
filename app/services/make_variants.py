"""Randomized Awwwards-caliber Figma Make creative variants.

Each Create Site click picks a fresh design direction while business + brand
data stay locked as hard constraints.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import Any


# Weighted: most runs combine video + 3D + imagery
MEDIA_MODES: list[tuple[str, int, str]] = [
    (
        "hybrid_triple",
        55,
        "HYBRID (default preference): combine cinematic video, interactive 3D, and editorial photography in one cohesive site. Video for hero/atmosphere, 3D for signature moments and service accents, photography for proof and place.",
    ),
    (
        "video_led",
        15,
        "VIDEO-LED: build the site around full-bleed / masked video storytelling — hero reel, scroll-scrubbed clips, ambient loops. Still use light 3D accents and photo proof, but video drives emotion.",
    ),
    (
        "3d_led",
        15,
        "3D-LED: WebGL/Three-style sculptural 3D is the star — hero sculpture, scroll-driven scenes, interactive objects. Support with short video moments and real photo references.",
    ),
    (
        "image_led",
        15,
        "IMAGE-LED: art-directed photography and kinetic image grids dominate — large crops, mask reveals, parallax stacks. Add subtle 3D marks and one short video accent, but images lead.",
    ),
]

LAYOUT_SYSTEMS = [
    "Asymmetric editorial magazine layout — oversized type colliding with media, uneven columns, intentional overlap",
    "Horizontal scroll storytelling chapters with sticky vertical nav and chapter markers",
    "Single-page kinetic scroll narrative with pinned scenes that morph between sections",
    "Split-screen duotone composition (media left / type right) that swaps on scroll",
    "Brutalist-modern hybrid: raw type hierarchy + polished motion and refined brand color blocking",
    "Immersive full-viewport scenes stacked like a film reel — each section is a new 'shot'",
    "Floating layered depth layout: translucent panels over full-bleed media with z-parallax",
    "Diagonal / broken-grid composition with offset modules and dramatic whitespace voids",
]

HERO_TREATMENTS = [
    "Cinematic full-bleed hero with masked type cutout revealing video/3D underneath",
    "Oversized wordmark entrance that dissolves into a 3D/product scene",
    "Scroll-scrub hero: dragging scroll advances a 3D or video sequence before unlocking the page",
    "Split hero — huge headline + magnetic CTA on one side, living media object on the other",
    "Dark atmospheric hero with a single lit 3D sculpture and ultra-minimal copy",
    "Kinetic typography hero where letters assemble around a floating media orb",
    "Photo-stack hero: layered real business photos peel/parallax into focus on load",
    "Video portal hero: circular/rect mask expands from logo into full-bleed reel",
]

ANIMATION_SYSTEMS = [
    "GSAP-like timeline mastery: staggered load, scrubbed scroll timelines, magnetic buttons, smooth page transitions",
    "Locomotive/lenis-style smooth scroll with pinned sections and progress-linked reveals",
    "WebGL particle / shader accents reacting to cursor + scroll (subtle, premium — not chaotic)",
    "Morphing SVG + 3D camera moves; section transitions feel like film cuts",
    "Micro-interaction dense UI: hover tilt, cursor follower, liquid button fills, underline draw",
    "Scroll-driven storytelling: opacity/blur/scale choreography with 3D idle floats",
    "Horizontal drag / scroll panels with snap points and inertia",
    "Clip-path and mask reveals synced to scroll velocity for editorial image moments",
]

COMPONENT_SETS = [
    "Magnetic CTA, infinite marquee of brand words, sticky glass nav, editorial quote slab, service 'orbit' cards",
    "Vertical chapter rail, fullscreen media slider, price ribbon, review ticker, map as designed graphic",
    "Bento-asymmetric media mosaic (not a boring grid), hover-expand service tiles, floating contact dock",
    "Accordion storytelling, before/after wipe, team/portrait strip, booking modal with motion",
    "3D icon carousel for services, parallax gallery, testimonial type-on-path, footer with logo lockup",
    "Filterable offering list with kinetic transitions, immersive map panel, FAQ morph section",
    "Horizontal case/service scroller, video chapter menu, proof counter that animates on view",
    "Layered depth cards, cursor-spotlight gallery, split contact form + live hours panel",
]

TYPOGRAPHY_APPROACHES = [
    "Expressive display face for headlines (huge, tight tracking) + refined grotesque body — never Inter/Roboto-only",
    "Editorial serif headlines + sharp modern sans UI; mixed scale for drama",
    "Condensed athletic / industrial display for energy categories; soft humanist for calm categories",
    "Variable font hero with weight/width morph on scroll; clean sans for readability",
    "Mixed case kinetic type with occasional vertical text accents and oversized numerals",
]

INTERACTION_SIGNATURES = [
    "Custom cursor that morphs over CTAs and media; magnetic primary button",
    "Scroll progress indicator as a brand-colored filament / timeline",
    "Hover 'lens' that sharpens or colorizes photo references",
    "Drag-to-explore 3D object in services or about",
    "Sound-optional mute toggle if video is present (default muted autoplay)",
    "Page transition wipe using brand color when jumping to contact",
]

SECTION_BLUEPRINTS = [
    ["hero", "marquee", "about", "services", "immersive", "proof", "contact"],
    ["hero", "immersive", "services", "about", "gallery", "proof", "contact"],
    ["hero", "proof", "services", "immersive", "about", "gallery", "contact"],
    ["hero", "marquee", "gallery", "services", "about", "immersive", "contact"],
    ["hero", "services", "video_moment", "about", "proof", "immersive", "contact"],
    ["hero", "about", "3d_moment", "services", "gallery", "proof", "contact"],
]

AWWWARDS_QUALITY = [
    "Benchmark quality against Awwwards Sites of the Day / Developer Award winners (https://www.awwwards.com/websites/): craft, originality, motion fluency, and unforgettable first viewport.",
    "Think Site of the Day caliber: intentional composition, premium motion, distinctive art direction — not a template.",
    "Match the ambition of award-winning WebGL / GSAP / storytelling sites: every scroll beat should feel designed.",
    "Jury-level polish: typography, spacing, media craft, and interaction must feel agency-made for THIS brand only.",
]


@dataclass
class MakeVariant:
    id: str
    media_mode: str
    media_brief: str
    layout: str
    hero: str
    animation: str
    components: str
    typography: str
    interaction: str
    sections: list[str]
    quality_bar: str
    seed_note: str

    def to_meta(self) -> dict[str, Any]:
        return {
            "variant_id": self.id,
            "media_mode": self.media_mode,
            "layout": self.layout[:80],
            "hero": self.hero[:80],
        }


def pick_media_mode(rng: random.Random) -> tuple[str, str]:
    names = [m[0] for m in MEDIA_MODES]
    weights = [m[1] for m in MEDIA_MODES]
    briefs = {m[0]: m[2] for m in MEDIA_MODES}
    choice = rng.choices(names, weights=weights, k=1)[0]
    return choice, briefs[choice]


def pick_make_variant(rng: random.Random | None = None) -> MakeVariant:
    rng = rng or random.Random()
    media_mode, media_brief = pick_media_mode(rng)
    return MakeVariant(
        id=uuid.uuid4().hex[:10],
        media_mode=media_mode,
        media_brief=media_brief,
        layout=rng.choice(LAYOUT_SYSTEMS),
        hero=rng.choice(HERO_TREATMENTS),
        animation=rng.choice(ANIMATION_SYSTEMS),
        components=rng.choice(COMPONENT_SETS),
        typography=rng.choice(TYPOGRAPHY_APPROACHES),
        interaction=rng.choice(INTERACTION_SIGNATURES),
        sections=list(rng.choice(SECTION_BLUEPRINTS)),
        quality_bar=rng.choice(AWWWARDS_QUALITY),
        seed_note=f"Creative seed {rng.randint(1000, 9999)} — invent a NEW composition; do not reuse a generic SaaS/marketing template.",
    )


def _section_copy(key: str, business_name: str, media_mode: str) -> list[str]:
    name = business_name
    catalog: dict[str, list[str]] = {
        "hero": [
            f"HERO — {name}",
            f"- Execute the assigned hero treatment. Brand name + one sharp line only in first viewport.",
            "- Official SVG logo in nav. One primary CTA. No stats strips or card clutter.",
        ],
        "marquee": [
            "MARQUEE / MOTION BAND",
            "- Animated brand-word marquee using content themes; seamless loop.",
        ],
        "about": [
            "ABOUT + PLACE",
            "- Asymmetric about: story + real Google photo references as art-directed plates.",
            "- Scroll-reveal; feel local and specific to this business.",
        ],
        "services": [
            "SERVICES / OFFERINGS",
            "- Interactive service modules (not flat icon rows). Tie each to brand colors.",
            "- Prefer 3D accents or kinetic media thumbs depending on media mode.",
        ],
        "immersive": [
            "IMMERSIVE FEATURE MOMENT",
            "- One showpiece section: scroll-driven 3D, video narrative, or photo cinema — matching media mode.",
        ],
        "video_moment": [
            "VIDEO CHAPTER",
            "- Dedicated video-led section (muted loop / scrub / portal). Atmosphere of the real business.",
        ],
        "3d_moment": [
            "3D SIGNATURE SCENE",
            "- Dedicated interactive or scroll-scrubbed 3D scene symbolizing what they sell/do.",
        ],
        "gallery": [
            "GALLERY / PROOF MEDIA",
            "- Kinetic gallery built from Google business photo references (see URLs). Art-direct crops; no random stock.",
        ],
        "proof": [
            "SOCIAL PROOF",
            "- Editorial reviews / rating moment with motion — not a generic carousel template.",
        ],
        "contact": [
            "CONTACT / BOOK",
            "- Premium form + phone/address/hours. Map integrated into the design language.",
            "- Footer repeats official SVG logo. Final media accent matching media mode.",
        ],
    }
    lines = list(catalog.get(key, [key.upper(), f"- Custom section for {name}."]))
    if media_mode == "video_led" and key == "hero":
        lines.append("- Hero must be primarily video-driven.")
    if media_mode == "3d_led" and key in ("hero", "immersive", "3d_moment"):
        lines.append("- Prioritize sculptural 3D craft here.")
    if media_mode == "image_led" and key in ("hero", "gallery", "about"):
        lines.append("- Prioritize large photographic storytelling from Google references.")
    return lines


def format_variant_block(variant: MakeVariant, business_name: str) -> list[str]:
    lines = [
        "",
        f"CREATIVE VARIANT ID: {variant.id} (unique this generation — do not recycle prior layouts)",
        variant.seed_note,
        "",
        "MEDIA DIRECTION (this build only):",
        f"- Mode: {variant.media_mode}",
        f"- {variant.media_brief}",
        "",
        "DESIGN SYSTEM FOR THIS BUILD:",
        f"- Layout system: {variant.layout}",
        f"- Hero treatment: {variant.hero}",
        f"- Animation system: {variant.animation}",
        f"- Signature interaction: {variant.interaction}",
        f"- Component palette: {variant.components}",
        f"- Typography approach: {variant.typography}",
        "",
        "PAGE BLUEPRINT (follow this section order):",
    ]
    for i, key in enumerate(variant.sections, start=1):
        letter = chr(ord("A") + i - 1)
        section_lines = _section_copy(key, business_name, variant.media_mode)
        lines.append("")
        lines.append(f"{letter}) {section_lines[0]}")
        lines.extend(section_lines[1:])
    return lines


def format_photo_references(photo_uris: list[str]) -> list[str]:
    if not photo_uris:
        return [
            "",
            "GOOGLE BUSINESS IMAGES:",
            "- No live photo URLs available. Invent photography that feels like authentic on-location shots for THIS business category and brand imagery style — never generic stock office/handshake clichés.",
        ]
    lines = [
        "",
        "REAL GOOGLE BUSINESS PHOTO REFERENCES (mandatory visual source):",
        "Use these real images as the primary reference for all site photography, hero frames, gallery, and video moodboards.",
        "Art-direct them (crop, grade to brand palette, mask, parallax) — do not invent unrelated stock faces/places.",
        "Create elevated site imagery inspired by these references; keep the real business recognizable.",
    ]
    for i, uri in enumerate(photo_uris, start=1):
        lines.append(f"- Photo {i}: {uri}")
    return lines
