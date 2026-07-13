// Read-only CUDA Green Context prerequisite inspection.
//
// This program initializes the CUDA driver only far enough to query device
// identity and attributes. It never creates a CUDA/Green Context, stream,
// event, partition, or workload. Green Context entry points are inspected via
// dlsym and are never invoked.
#include <cuda.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>

#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

namespace {

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  for (const unsigned char ch : value) {
    switch (ch) {
      case '\\': out << "\\\\"; break;
      case '"': out << "\\\""; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (ch < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
              << static_cast<int>(ch) << std::dec;
        } else {
          out << ch;
        }
    }
  }
  return out.str();
}

std::string uuid_text(const CUuuid& uuid) {
  const auto* bytes = reinterpret_cast<const unsigned char*>(uuid.bytes);
  std::ostringstream out;
  out << "GPU-" << std::hex << std::setfill('0');
  for (int index = 0; index < 16; ++index) {
    if (index == 4 || index == 6 || index == 8 || index == 10) out << '-';
    out << std::setw(2) << static_cast<unsigned int>(bytes[index]);
  }
  return out.str();
}

bool symbol_present(void* library, const char* name) {
  return library != nullptr && dlsym(library, name) != nullptr;
}

const char* boolean(bool value) { return value ? "true" : "false"; }

int fail(const std::string& stage, int code, const std::string& message) {
  std::cout << "{\n"
            << "  \"schema_version\": 1,\n"
            << "  \"operation\": \"green_context_prerequisite_inspect\",\n"
            << "  \"mutated_state\": false,\n"
            << "  \"ok\": false,\n"
            << "  \"error\": {\"stage\": \"" << json_escape(stage)
            << "\", \"code\": " << code << ", \"message\": \""
            << json_escape(message) << "\"}\n"
            << "}\n";
  return 1;
}

}  // namespace

int main() {
  void* runtime_library = dlopen("libcudart.so.13", RTLD_NOW | RTLD_LOCAL);
  if (runtime_library == nullptr) {
    const char* error = dlerror();
    return fail("dlopen_libcudart", 1, error ? error : "libcudart.so.13 unavailable");
  }
  void* driver_library = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
  if (driver_library == nullptr) {
    const char* error = dlerror();
    return fail("dlopen_libcuda", 1, error ? error : "libcuda.so.1 unavailable");
  }

  int runtime_version = 0;
  cudaError_t runtime_result = cudaRuntimeGetVersion(&runtime_version);
  if (runtime_result != cudaSuccess) {
    return fail("cudaRuntimeGetVersion", static_cast<int>(runtime_result),
                cudaGetErrorString(runtime_result));
  }

  CUresult result = cuInit(0);
  if (result != CUDA_SUCCESS) {
    const char* message = nullptr;
    cuGetErrorString(result, &message);
    return fail("cuInit", static_cast<int>(result), message ? message : "unknown driver error");
  }

  int driver_version = 0;
  result = cuDriverGetVersion(&driver_version);
  if (result != CUDA_SUCCESS) return fail("cuDriverGetVersion", result, "driver query failed");

  int device_count = 0;
  result = cuDeviceGetCount(&device_count);
  if (result != CUDA_SUCCESS) return fail("cuDeviceGetCount", result, "device count failed");
  if (device_count != 1) {
    return fail("cuDeviceGetCount", device_count,
                "expected exactly one UUID-selected visible GPU");
  }

  CUdevice device = 0;
  result = cuDeviceGet(&device, 0);
  if (result != CUDA_SUCCESS) return fail("cuDeviceGet", result, "device lookup failed");

  char name[256] = {};
  CUuuid uuid{};
  int major = 0;
  int minor = 0;
  int sm_count = 0;
  if (cuDeviceGetName(name, sizeof(name), device) != CUDA_SUCCESS ||
      cuDeviceGetUuid(&uuid, device) != CUDA_SUCCESS ||
      cuDeviceGetAttribute(&major, CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR, device) != CUDA_SUCCESS ||
      cuDeviceGetAttribute(&minor, CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR, device) != CUDA_SUCCESS ||
      cuDeviceGetAttribute(&sm_count, CU_DEVICE_ATTRIBUTE_MULTIPROCESSOR_COUNT, device) != CUDA_SUCCESS) {
    return fail("device_attributes", 1, "one or more device attribute queries failed");
  }

  const char* visible = std::getenv("CUDA_VISIBLE_DEVICES");
  std::cout << "{\n"
            << "  \"schema_version\": 1,\n"
            << "  \"operation\": \"green_context_prerequisite_inspect\",\n"
            << "  \"mutated_state\": false,\n"
            << "  \"created_context\": false,\n"
            << "  \"launched_workload\": false,\n"
            << "  \"ok\": true,\n"
            << "  \"runtime_version\": " << runtime_version << ",\n"
            << "  \"driver_version\": " << driver_version << ",\n"
            << "  \"cuda_visible_devices\": \""
            << json_escape(visible ? visible : "") << "\",\n"
            << "  \"gpu\": {\n"
            << "    \"uuid\": \"" << uuid_text(uuid) << "\",\n"
            << "    \"name\": \"" << json_escape(name) << "\",\n"
            << "    \"compute_capability\": \"" << major << '.' << minor << "\",\n"
            << "    \"sm_count\": " << sm_count << "\n"
            << "  },\n"
            << "  \"symbols\": {\n"
            << "    \"cudaGreenCtxCreate\": " << boolean(symbol_present(runtime_library, "cudaGreenCtxCreate")) << ",\n"
            << "    \"cudaDevSmResourceSplitByCount\": " << boolean(symbol_present(runtime_library, "cudaDevSmResourceSplitByCount")) << ",\n"
            << "    \"cudaDeviceGetDevResource\": " << boolean(symbol_present(runtime_library, "cudaDeviceGetDevResource")) << ",\n"
            << "    \"cudaDevResourceGenerateDesc\": " << boolean(symbol_present(runtime_library, "cudaDevResourceGenerateDesc")) << ",\n"
            << "    \"cudaExecutionCtxStreamCreate\": " << boolean(symbol_present(runtime_library, "cudaExecutionCtxStreamCreate")) << ",\n"
            << "    \"cudaExecutionCtxGetDevResource\": " << boolean(symbol_present(runtime_library, "cudaExecutionCtxGetDevResource")) << ",\n"
            << "    \"cuGreenCtxCreate\": " << boolean(symbol_present(driver_library, "cuGreenCtxCreate")) << "\n"
            << "  }\n"
            << "}\n";
  return 0;
}
