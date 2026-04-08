import React, { useState, useImperativeHandle, forwardRef, useMemo } from "react";
import { Box, Text, useInput } from "ink";

export interface ScrollBoxHandle {
  scrollTo: (position: number) => void;
  scrollBy: (delta: number) => void;
  scrollToEnd: () => void;
}

interface ScrollBoxProps {
  height: number;
  children: React.ReactNode[];
  stickyScroll?: boolean;
  isActive?: boolean;
}

export const ScrollBox = forwardRef<ScrollBoxHandle, ScrollBoxProps>(
  function ScrollBox({ height, children, stickyScroll = false, isActive = true }, ref) {
    const totalItems = React.Children.count(children);
    const [scrollOffset, setScrollOffset] = useState(
      stickyScroll ? Math.max(0, totalItems - height) : 0
    );

    const maxOffset = Math.max(0, totalItems - height);

    useImperativeHandle(ref, () => ({
      scrollTo(position: number) {
        setScrollOffset(Math.max(0, Math.min(position, maxOffset)));
      },
      scrollBy(delta: number) {
        setScrollOffset((prev) => Math.max(0, Math.min(prev + delta, maxOffset)));
      },
      scrollToEnd() {
        setScrollOffset(maxOffset);
      },
    }));

    useInput(
      (input, key) => {
        if (key.upArrow) {
          setScrollOffset((prev) => Math.max(0, prev - 1));
        } else if (key.downArrow) {
          setScrollOffset((prev) => Math.min(maxOffset, prev + 1));
        } else if (key.pageUp) {
          setScrollOffset((prev) => Math.max(0, prev - height));
        } else if (key.pageDown) {
          setScrollOffset((prev) => Math.min(maxOffset, prev + height));
        }
      },
      { isActive }
    );

    const visibleChildren = useMemo(() => {
      const arr = React.Children.toArray(children);
      return arr.slice(scrollOffset, scrollOffset + height);
    }, [children, scrollOffset, height]);

    const showScrollbar = totalItems > height;

    return (
      <Box flexDirection="column">
        {visibleChildren}
        {showScrollbar && (
          <Text color="gray" dimColor>
            ↕ {scrollOffset + 1}-{Math.min(scrollOffset + height, totalItems)}/{totalItems}
          </Text>
        )}
      </Box>
    );
  }
);
