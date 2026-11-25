{{/*
Expand the name of the chart.
*/}}
{{- define "jarvis-registry.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "jarvis-registry.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "jarvis-registry.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "jarvis-registry.labels" -}}
helm.sh/chart: {{ include "jarvis-registry.chart" . }}
{{ include "jarvis-registry.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "jarvis-registry.selectorLabels" -}}
app.kubernetes.io/name: {{ include "jarvis-registry.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image tag for the registry's own images. Falls back to .Chart.AppVersion
when registry.imageTag is unset, so a chart bump moves all three images in
lockstep without a values override.
*/}}
{{- define "jarvis-registry.imageTag" -}}
{{- .Values.registry.imageTag | default .Chart.AppVersion -}}
{{- end }}

{{/*
Service account name for registry workloads.
Defaults to <fullname>-sa; override with registry.serviceAccount.name.
*/}}
{{- define "jarvis-registry.serviceAccountName" -}}
{{- .Values.registry.serviceAccount.name | default (printf "%s-sa" (include "jarvis-registry.fullname" .)) }}
{{- end }}

{{/*
Service account name for the External Secrets Operator.
Defaults to <fullname>-eso-sa; override with registry.externalSecrets.serviceAccount.name.
*/}}
{{- define "jarvis-registry.externalSecretsServiceAccountName" -}}
{{- .Values.registry.externalSecrets.serviceAccount.name | default (printf "%s-eso-sa" (include "jarvis-registry.fullname" .)) }}
{{- end }}
