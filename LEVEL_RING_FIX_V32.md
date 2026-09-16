# Level Ring Fix V32

Fixed the level ring around the level number in the player card.

- SVG viewBox is now 112x112.
- Both circles use the same center: 56,56.
- Both circles use radius 49.
- SVG has no positional/rotation transform.
- The level badge uses the exact same 112x112 coordinate box.
- Removed legacy pseudo-circles that could hide or offset the ring.
- JS circumference is synchronized to r=49.
- Ring track is visible and progress is green, matching the original UI.
