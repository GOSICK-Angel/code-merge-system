import ReactDiffViewer, { DiffMethod } from "react-diff-viewer-continued";

interface Props {
  oldLabel: string;
  newLabel: string;
  oldText: string;
  newText: string;
}

/**
 * Side-by-side diff with dark-theme tokens that match the rest of the L1/L3
 * shell. ``react-diff-viewer-continued`` accepts a ``styles`` prop for
 * inline overrides — we set just enough to escape the bright default
 * palette without forking a full theme.
 */
export function DiffViewer({
  oldLabel,
  newLabel,
  oldText,
  newText,
}: Props): JSX.Element {
  return (
    <div className="text-[12px] font-mono">
      <ReactDiffViewer
        oldValue={oldText}
        newValue={newText}
        leftTitle={oldLabel}
        rightTitle={newLabel}
        splitView
        compareMethod={DiffMethod.LINES}
        useDarkTheme
        styles={{
          variables: {
            dark: {
              diffViewerBackground: "#0b1220",
              diffViewerColor: "#e6edf3",
              gutterBackground: "#0b1220",
              gutterColor: "#475569",
              addedBackground: "#064e3b",
              removedBackground: "#7f1d1d",
              codeFoldGutterBackground: "#1e293b",
              codeFoldBackground: "#1e293b",
            },
          },
        }}
      />
    </div>
  );
}
