declare const __VELA_SOURCE_ID__: string;

export const BUILD_ID = typeof __VELA_SOURCE_ID__ === 'string' ? __VELA_SOURCE_ID__ : 'unbuilt';
