import React, { Component } from "react";
import { Box, Text } from "ink";

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <Box
          flexDirection="column"
          borderStyle="single"
          borderColor="red"
          paddingX={1}
          paddingY={1}
        >
          <Text bold color="red">
            Render Error
          </Text>
          <Text color="red">{this.state.error.message}</Text>
          <Text color="gray">{this.state.error.stack?.split("\n").slice(0, 3).join("\n")}</Text>
          <Text color="yellow">Press q to quit</Text>
        </Box>
      );
    }

    return this.props.children;
  }
}
