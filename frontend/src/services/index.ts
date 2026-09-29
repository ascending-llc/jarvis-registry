import ACL from './acl';
import AGENT from './agent';
import AUTH from './auth';
import FEDERATION from './federation';
import MCP from './mcp';
import SERVER from './server';
import SKILL from './skill';
import SKILL_SYNC_SOURCE from './skillSyncSource';
import WORKFLOW from './workflow';

const SERVICE = { AGENT, AUTH, MCP, SERVER, SKILL, SKILL_SYNC_SOURCE, ACL, FEDERATION, WORKFLOW };

export default SERVICE;
