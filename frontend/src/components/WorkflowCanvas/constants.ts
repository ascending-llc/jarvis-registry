import { MarkerType } from '@xyflow/react';

export const EDGE_CONFIG = {
  markerEnd: { type: MarkerType.ArrowClosed, color: '#7c3aed' },
  style: { stroke: 'rgba(124,58,237,0.55)', strokeWidth: 1.5, strokeDasharray: '5,5' },
};

export const DASHED_EDGE = {
  style: { stroke: 'rgba(124,58,237,0.2)', strokeWidth: 1.5, strokeDasharray: '5,5' },
};

export const NODE_WIDTH = 220;
export const NODE_HEIGHT_DEFAULT = 90;
export const HANDLE_SPACING = 36;
export const BRANCH_CANVAS_SPACING = NODE_HEIGHT_DEFAULT + 50;
export const BRANCH_SPACING = 120;
export const ADD_NODE_MARGIN_X = 48;
