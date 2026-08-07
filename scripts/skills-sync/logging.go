package main

import (
	"fmt"
	"io"
	"log"
	"os"
	"strings"
)

type logLevel int

const (
	logLevelDebug logLevel = iota
	logLevelInfo
	logLevelWarn
	logLevelError
)

type syncLogger struct {
	minimumLevel logLevel
	output       *log.Logger
}

func envLogLevel(name string) (logLevel, error) {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return logLevelInfo, nil
	}
	return parseLogLevel(value)
}

func parseLogLevel(value string) (logLevel, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "debug":
		return logLevelDebug, nil
	case "info":
		return logLevelInfo, nil
	case "warn", "warning":
		return logLevelWarn, nil
	case "error":
		return logLevelError, nil
	default:
		return logLevelInfo, fmt.Errorf("%s must be debug, info, warn, or error, got %q", logLevelEnvName, value)
	}
}

func newSyncLogger(minimumLevel logLevel, writer io.Writer) *syncLogger {
	return &syncLogger{
		minimumLevel: minimumLevel,
		output:       log.New(writer, "skills-sync ", log.LstdFlags|log.Lmicroseconds),
	}
}

func (logger *syncLogger) logf(level logLevel, label string, format string, arguments ...any) {
	if logger == nil || level < logger.minimumLevel {
		return
	}
	logger.output.Printf("%s %s", label, fmt.Sprintf(format, arguments...))
}

func debugf(logger *syncLogger, format string, arguments ...any) {
	logger.logf(logLevelDebug, "DEBUG", format, arguments...)
}

func infof(logger *syncLogger, format string, arguments ...any) {
	logger.logf(logLevelInfo, "INFO", format, arguments...)
}

func warnf(logger *syncLogger, format string, arguments ...any) {
	logger.logf(logLevelWarn, "WARN", format, arguments...)
}
