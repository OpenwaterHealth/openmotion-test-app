// FpgaModel.js

// Runtime scale overrides – keyed as "<fpgaLabel>|<funcName>"
// Populated from user config on console connect so display and write
// conversions use the device-stored scale instead of the static default.
var scaleOverrides = {};

/**
 * Return the effective scale for a given FPGA label + function name.
 * Falls back to the static model value if no override exists.
 */
function getScale(fpgaLabel, funcName) {
    var key = fpgaLabel + "|" + funcName;
    if (scaleOverrides.hasOwnProperty(key))
        return scaleOverrides[key];
    var fpga = fpgaAddressModel.find(function(f) { return f.label === fpgaLabel; });
    if (!fpga) return 1.0;
    var fn = fpga.functions.find(function(f) { return f.name === funcName; });
    return (fn && fn.scale) ? fn.scale : 1.0;
}

/**
 * Set a runtime scale override. Pass scale <= 0 to remove it and revert
 * to the static model default.
 */
function setScaleOverride(fpgaLabel, funcName, scale) {
    var key = fpgaLabel + "|" + funcName;
    if (scale > 0) scaleOverrides[key] = scale;
    else           delete scaleOverrides[key];
}

var fpgaAddressModel = [
    {
        label: "TA",
        mux_idx: 1,
        channel: 4,
        i2c_addr: 0x41,
        isMsbFirst: false,
        functions: [
            { name: "PULSE WIDTH", desc: "Pulse Width", start_address: 0x00, data_size: "24B", direction: "RW", unit: "us", scale: 0.320 },
            { name: "PERIOD", desc: "Period", start_address: 0x03, data_size: "24B", direction: "RW", unit: "us", scale: 0.320 },
            { name: "CURRENT DRV", desc: "Current Drive", start_address: 0x06, data_size: "16B", direction: "RW", unit: "mA", scale: 0.160 },
            { name: "CURRENT LIMIT", desc: "Current Limit", start_address: 0x08, data_size: "16B", direction: "RW", unit: "mA", scale: 0.160 },
            { name: "PWM MON CL", desc: "PWM Monitor Current Limit", start_address: 0x0A, data_size: "16B", direction: "RW", unit: "mv", scale: 0.500 },
            { name: "CW MON CL", desc: "CW Monitor Current Limit", start_address: 0x0C, data_size: "16B", direction: "RW", unit: "mv", scale: 0.500 },
            { name: "TEMP Sensor", desc: "Temperature Sensor", start_address: 0x0E, data_size: "16B", direction: "RW" },
            { name: "TRIGGER COUNT", desc: "Trigger Count", start_address: 0x10, data_size: "32B", direction: "RD" },
            { name: "REVISION", desc: "Revision Version", start_address: 0x14, data_size: "8B", direction: "RD" },
            { name: "MINOR", desc: "Minor Version", start_address: 0x15, data_size: "8B", direction: "RD" },
            { name: "MAJOR", desc: "Major Version", start_address: 0x16, data_size: "8B", direction: "RD" },
            { name: "ID", desc: "FPGA ID", start_address: 0x17, data_size: "8B", direction: "RD" },
            { name: "STATIC CTL", desc: "Static Control", start_address: 0x20, data_size: "16B", direction: "RW" },
            { name: "DYNAMIC CTL", desc: "Dynamic Control", start_address: 0x22, data_size: "16B", direction: "RW" },
            { name: "STATUS", desc: "Status Register", start_address: 0x24, data_size: "8B", direction: "RW" }
        ]
    },
    {
        label: "Seed",
        mux_idx: 1,
        channel: 5,
        i2c_addr: 0x41,
        isMsbFirst: false,
        functions: [
            { name: "DDS CTRL", desc: "DDS Control", start_address: 0x00, data_size: "16B", direction: "RW" },
            { name: "DDS GAIN", desc: "DDS Gain", start_address: 0x02, data_size: "16B", direction: "RW", unit: "mV", scale: 0.064 },
            { name: "CW GAIN", desc: "CW Gain", start_address: 0x04, data_size: "16B", direction: "RW", unit: "mV", scale: 0.0688 },
            { name: "DDS CL", desc: "DDS Current Limit", start_address: 0x06, data_size: "16B", direction: "RW", unit: "mA", scale: 0.081 },
            { name: "CW CL", desc: "CW Current Limit", start_address: 0x08, data_size: "16B", direction: "RW", unit: "mA", scale: 0.079 },
            { name: "ADC DDS CL", desc: "ADC DDS Current Limit", start_address: 0x0A, data_size: "16B", direction: "RW", unit: "mv", scale: 0.500 },
            { name: "ADC CW CL", desc: "ADC CW Current Limit", start_address: 0x0C, data_size: "16B", direction: "RW", unit: "mv", scale: 0.500 },
            { name: "ADC CD", desc: "ADC Current Data", start_address: 0x0E, data_size: "16B", direction: "RD", unit: "mv", scale: 0.500 },
            { name: "ADC VD", desc: "ADC Voltage Data", start_address: 0x10, data_size: "16B", direction: "RD", unit: "mv", scale: 0.500 },
            { name: "STATUS", desc: "Status", start_address: 0x12, data_size: "8B", direction: "RD" },
            { name: "REVISION", desc: "Revision Version", start_address: 0x13, data_size: "8B", direction: "RD" },
            { name: "MINOR", desc: "Minor Version", start_address: 0x14, data_size: "8B", direction: "RD" },
            { name: "MAJOR", desc: "Major Version", start_address: 0x15, data_size: "8B", direction: "RD" },
            { name: "ID", desc: "FPGA ID", start_address: 0x16, data_size: "8B", direction: "RD" },
            { name: "STATIC CTRL", desc: "Static Control", start_address: 0x20, data_size: "16B", direction: "RW" },
            { name: "DYNAMIC CTRL", desc: "Dynamic Control", start_address: 0x22, data_size: "16B", direction: "WR" }
        ]
    },
    {
        label: "Safety EE",
        mux_idx: 1,
        channel: 6,
        i2c_addr: 0x41,
        isMsbFirst: false,
        functions: [
            { name: "PULSE WIDTH LL", desc: "Pulse Width Lower Limit", start_address: 0x00, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "PULSE WIDTH UL", desc: "Pulse Width Upper Limit", start_address: 0x04, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "RATE LL", desc: "Rate Lower Limit", start_address: 0x08, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "RATE UL", desc: "Rate Upper Limit", start_address: 0x0C, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "DRIVE CL", desc: "Drive Current Limit", start_address: 0x10, data_size: "16B", direction: "RW", unit: "mA", scale: 1.86 },
            { name: "PWM CURRENT", desc: "PWM Drive Current", start_address: 0x12, data_size: "16B", direction: "RW", unit: "mA", scale: 0.160 },
            { name: "CW CURRENT", desc: "CW Drive Current", start_address: 0x14, data_size: "16B", direction: "RW", unit: "mA", scale: 0.160 },
            { name: "PWM MONITOR CL", desc: "PWM Monitor Current Limit", start_address: 0x16, data_size: "16B", direction: "RW", unit: "mA", scale: 0.025 },
            { name: "CW MONITOR CL", desc: "CW Monitor Current Limit", start_address: 0x18, data_size: "16B", direction: "RW", unit: "mA", scale: 0.025 },
            { name: "TEMP Sensor", desc: "Temperature Sensor", start_address: 0x1A, data_size: "16B", direction: "RW" },
            { name: "ADC DATA", desc: "ADC Data", start_address: 0x1C, data_size: "16B", direction: "RD", unit: "mA", scale: 2.500 },
            { name: "STATIC CTRL", desc: "Static control bits", start_address: 0x20, data_size: "16B", direction: "RW" },
            { name: "DYNAMIC CTRL", desc: "Dynamic control bits", start_address: 0x22, data_size: "16B", direction: "WR" },
            { name: "STATUS", desc: "Status Register", start_address: 0x24, data_size: "8B", direction: "RW" },
            { name: "REVISION", desc: "Revision Version", start_address: 0x25, data_size: "8B", direction: "RD" },
            { name: "MINOR", desc: "Minor Version", start_address: 0x26, data_size: "8B", direction: "RD" },
            { name: "MAJOR", desc: "Major Version", start_address: 0x27, data_size: "8B", direction: "RD" },
            { name: "ID", desc: "FPGA ID", start_address: 0x28, data_size: "8B", direction: "RD" }
        ]
    },
    {
        label: "Safety OPT",
        mux_idx: 1,
        channel: 7,
        i2c_addr: 0x41,
        isMsbFirst: false,
        functions: [
            { name: "PULSE WIDTH LL", desc: "Pulse Width Lower Limit", start_address: 0x00, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "PULSE WIDTH UL", desc: "Pulse Width Upper Limit", start_address: 0x04, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "RATE LL", desc: "Rate Lower Limit", start_address: 0x08, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "RATE UL", desc: "Rate Upper Limit", start_address: 0x0C, data_size: "32B", direction: "RW", unit: "uS", scale: 0.320 },
            { name: "DRIVE CL", desc: "Drive Current Limit", start_address: 0x10, data_size: "16B", direction: "RW", unit: "mA", scale: 1.86 },
            { name: "PWM CURRENT", desc: "PWM Drive Current", start_address: 0x12, data_size: "16B", direction: "RW", unit: "mA", scale: 0.160 },
            { name: "CW CURRENT", desc: "CW Drive Current", start_address: 0x14, data_size: "16B", direction: "RW", unit: "mA", scale: 0.160 },
            { name: "PWM MONITOR CL", desc: "PWM Monitor Current Limit", start_address: 0x16, data_size: "16B", direction: "RW", unit: "mA", scale: 0.025 },
            { name: "CW MONITOR CL", desc: "CW Monitor Current Limit", start_address: 0x18, data_size: "16B", direction: "RW", unit: "mA", scale: 0.025 },
            { name: "TEMP Sensor", desc: "Temperature Sensor", start_address: 0x1A, data_size: "16B", direction: "RW" },
            { name: "ADC DATA", desc: "ADC Data", start_address: 0x1C, data_size: "16B", direction: "RD", unit: "mA", scale: 2.500 },
            { name: "STATIC CTRL", desc: "Static control bits", start_address: 0x20, data_size: "16B", direction: "RW" },
            { name: "DYNAMIC CTRL", desc: "Dynamic control bits", start_address: 0x22, data_size: "16B", direction: "WR" },
            { name: "STATUS", desc: "Status Register", start_address: 0x24, data_size: "8B", direction: "RW" },
            { name: "REVISION", desc: "Revision Version", start_address: 0x25, data_size: "8B", direction: "RD" },
            { name: "MINOR", desc: "Minor Version", start_address: 0x26, data_size: "8B", direction: "RD" },
            { name: "MAJOR", desc: "Major Version", start_address: 0x27, data_size: "8B", direction: "RD" },
            { name: "ID", desc: "FPGA ID", start_address: 0x28, data_size: "8B", direction: "RD" }
        ]
    }
];
