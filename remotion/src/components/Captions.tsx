import type { Caption, CaptionStyleType } from "../types";
import { BoldCaptions } from "./BoldCaptions";
import { BounceCaptions } from "./BounceCaptions";
import { CleanCaptions } from "./CleanCaptions";

interface CaptionsProps {
  captions: Caption[];
  style: CaptionStyleType;
  /** Vertical centre of a one-line caption in px (0-1920). Default: style's own position. */
  centerY?: number | null;
}

// The caption renderers anchor themselves 350px above the bottom of their
// container. To move them, shrink the container instead of touching each style.
const STYLE_BOTTOM = 350;
const HALF_LINE = 45;

/**
 * Style dispatcher — routes to the correct caption renderer.
 */
export const Captions: React.FC<CaptionsProps> = ({ captions, style, centerY }) => {
  if (!captions || captions.length === 0) return null;

  let inner: React.ReactNode;
  switch (style) {
    case "bounce":
      inner = <BounceCaptions captions={captions} />;
      break;
    case "clean":
      inner = <CleanCaptions captions={captions} />;
      break;
    default:
      inner = <BoldCaptions captions={captions} />;
  }
  if (centerY == null) return <>{inner}</>;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        height: centerY + HALF_LINE + STYLE_BOTTOM,
      }}
    >
      {inner}
    </div>
  );
};
