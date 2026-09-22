import { join } from "node:path";

export const DEFAULT_ENDPOINT = "http://127.0.0.1:8088";
export const DEFAULT_SERVICE_NAME = "minimal-agent";
export const DEFAULT_AGENT_ROOT = join(process.cwd(), "examples", "python", "minimal-agent");
export const DEFAULT_MODEL_DEPLOYMENT = "gpt-6-astra";
export const DEFAULT_TOOLBOX_NAME = "contract-toolbox";
