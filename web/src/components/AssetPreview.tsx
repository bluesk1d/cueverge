import { AssetRecord, assetArtifactUrl } from "../api";

export function AssetPreview({ asset }: { asset: AssetRecord }) {
  const hasReview = hasAssetArtifact(asset, "review");
  const hasThumbnail = hasAssetArtifact(asset, "thumbnail");
  const hasWaveform = hasAssetArtifact(asset, "waveform");
  if (asset.media_type?.startsWith("video/") && hasReview) {
    return <div className="asset-preview asset-preview--video">
      <video controls poster={hasThumbnail ? assetArtifactUrl(asset.id, "thumbnail", asset.current_revision) : undefined}
        preload="metadata" src={assetArtifactUrl(asset.id, "review", asset.current_revision)}>
        Review proxy playback is unsupported by this browser.
      </video>
      {hasWaveform && <img alt={`Waveform for ${asset.name}`} src={assetArtifactUrl(asset.id, "waveform", asset.current_revision)} />}
    </div>;
  }
  if (asset.media_type?.startsWith("audio/") && hasReview) {
    return <div className="asset-preview asset-preview--audio">
      {hasWaveform && <img alt={`Waveform for ${asset.name}`} src={assetArtifactUrl(asset.id, "waveform", asset.current_revision)} />}
      <audio controls preload="metadata" src={assetArtifactUrl(asset.id, "review", asset.current_revision)}>
        Review proxy playback is unsupported by this browser.
      </audio>
    </div>;
  }
  if (asset.media_type?.startsWith("image/") && hasReview) {
    return <img alt={`Review proxy for ${asset.name}`} className="asset-preview"
      loading="lazy" src={assetArtifactUrl(asset.id, "review", asset.current_revision)} />;
  }
  return <div className="asset-preview asset-preview--pending" aria-hidden="true" />;
}

export function hasAssetArtifact(asset: AssetRecord, kind: string): boolean {
  const artifacts = asset.metadata?.artifacts;
  return typeof artifacts === "object" && artifacts !== null && !Array.isArray(artifacts)
    && kind in artifacts;
}
