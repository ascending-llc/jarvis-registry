{{/*
Otel-collector `exporters:` block entries for the selected
registry.monitoring.backend. Each template defines its own `debug:` exporter
alongside this (verbosity differs per collector) and references it by name
in *ExporterNames below.
*/}}
{{- define "jarvis-registry.monitoring.exporterDefs" -}}
{{- $b := .Values.registry.monitoring.backend -}}
{{- $backends := .Values.registry.monitoring.backends -}}
{{- if eq $b "prometheus" }}
otlp/tempo:
  endpoint: {{ $backends.prometheus.tempoEndpoint | quote }}
  tls:
    insecure: true
prometheusremotewrite:
  endpoint: {{ $backends.prometheus.remoteWriteUrl | quote }}
  tls:
    insecure: true
    insecure_skip_verify: true
{{- else if eq $b "datadog" }}
datadog:
  api:
    site: {{ $backends.datadog.site | quote }}
    key: ${env:DD_API_KEY}
{{- else if eq $b "splunk" }}
splunk_hec/traces:
  endpoint: {{ $backends.splunk.hecEndpoint | quote }}
  token: ${env:SPLUNK_HEC_TOKEN}
  source: otel-collector
  sourcetype: otel:traces
splunk_hec/metrics:
  endpoint: {{ $backends.splunk.hecEndpoint | quote }}
  token: ${env:SPLUNK_HEC_TOKEN}
  source: otel-collector
  sourcetype: otel:metrics
{{- else if eq $b "dynatrace" }}
otlphttp/dynatrace:
  endpoint: {{ $backends.dynatrace.endpoint | quote }}
  headers:
    Authorization: "Api-Token ${env:DYNATRACE_API_TOKEN}"
{{- end }}
{{- end }}

{{/*
Exporter name(s) for the traces pipeline, matching registry.monitoring.backend.
*/}}
{{- define "jarvis-registry.monitoring.tracesExporterNames" -}}
{{- $b := .Values.registry.monitoring.backend -}}
{{- if eq $b "prometheus" -}}debug, otlp/tempo
{{- else if eq $b "datadog" -}}debug, datadog
{{- else if eq $b "splunk" -}}debug, splunk_hec/traces
{{- else if eq $b "dynatrace" -}}debug, otlphttp/dynatrace
{{- end -}}
{{- end }}

{{/*
Exporter name(s) for the metrics pipeline, matching registry.monitoring.backend.
*/}}
{{- define "jarvis-registry.monitoring.metricsExporterNames" -}}
{{- $b := .Values.registry.monitoring.backend -}}
{{- if eq $b "prometheus" -}}debug, prometheusremotewrite
{{- else if eq $b "datadog" -}}debug, datadog
{{- else if eq $b "splunk" -}}debug, splunk_hec/metrics
{{- else if eq $b "dynatrace" -}}debug, otlphttp/dynatrace
{{- end -}}
{{- end }}

{{/*
Env vars a collector CR needs to resolve the ${env:...} references used in
exporterDefs above — sourced from a Secret, never inlined as plaintext.
Empty for the prometheus backend (no credentials required).
*/}}
{{- define "jarvis-registry.monitoring.backendEnv" -}}
{{- $b := .Values.registry.monitoring.backend -}}
{{- $backends := .Values.registry.monitoring.backends -}}
{{- $targetSecret := .Values.registry.monitoring.externalSecrets.targetSecretName -}}
{{- if eq $b "datadog" }}
- name: DD_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $targetSecret }}
      key: {{ $backends.datadog.apiKeySecretKey }}
{{- else if eq $b "splunk" }}
- name: SPLUNK_HEC_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ $targetSecret }}
      key: {{ $backends.splunk.hecTokenSecretKey }}
{{- else if eq $b "dynatrace" }}
- name: DYNATRACE_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ $targetSecret }}
      key: {{ $backends.dynatrace.apiTokenSecretKey }}
{{- end }}
{{- end }}

{{/*
Which named keys the monitoring ExternalSecret needs to fetch, given which
features are currently enabled. Returns one key name per line (e.g.
"DD_API_KEY"); used to build the ExternalSecret's `data:` list so only the
keys actually in use are ever requested from AWS Secrets Manager.
*/}}
{{- define "jarvis-registry.monitoring.neededSecretKeys" -}}
{{- $m := .Values.registry.monitoring -}}
{{- $b := $m.backend -}}
{{- if eq $b "datadog" }}
{{ $m.backends.datadog.apiKeySecretKey }}
{{- else if eq $b "splunk" }}
{{ $m.backends.splunk.hecTokenSecretKey }}
{{- else if eq $b "dynatrace" }}
{{ $m.backends.dynatrace.apiTokenSecretKey }}
{{- end }}
{{- if $m.langfuse.enabled }}
{{ $m.langfuse.authorizationSecretKey }}
{{- end }}
{{- if $m.otelGateway.enabled }}
{{ $m.otelGateway.authSecretKey }}
{{- end }}
{{- if and (eq $b "prometheus") $m.grafana.enabled $m.grafana.smtp.enabled }}
{{ $m.grafana.smtp.userSecretKey }}
{{ $m.grafana.smtp.passwordSecretKey }}
{{- end }}
{{- end }}
