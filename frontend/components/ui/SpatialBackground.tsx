/**
 * Fundo dinâmico atrás das superfícies de vidro: orbes de neon que derivam
 * devagar, uma grade em perspectiva e um brilho que segue o cursor.
 * As variáveis --px/--py (paralaxe) e --cx/--cy (cursor) são escritas só neste
 * elemento por GlassPointerEffects, para não recalcular o estilo da página toda.
 */
export default function SpatialBackground() {
  return (
    <div id="q1-spatial-bg" className="spatial-bg" aria-hidden="true">
      <div className="spatial-bg-parallax">
        <span className="spatial-orb spatial-orb-1" />
        <span className="spatial-orb spatial-orb-2" />
        <span className="spatial-orb spatial-orb-3" />
        <span className="spatial-orb spatial-orb-4" />
      </div>
      <div className="spatial-grid" />
      <span className="spatial-cursor-glow" />
    </div>
  );
}
