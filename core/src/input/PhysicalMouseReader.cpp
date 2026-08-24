// PhysicalMouseReader.cpp — Logitech/USB 鼠标真实事件读取。
#include "input/PhysicalMouseReader.hpp"
#if !defined(_WIN32)
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <linux/input.h>
#include <cstring>
#endif
namespace ttbox::core::input {
bool PhysicalMouseReader::find_device(std::string* out) const {
#if defined(_WIN32)
 (void)out; return false;
#else
 DIR* d=opendir("/sys/class/input"); if(!d)return false; dirent* e;
 while((e=readdir(d))){ if(strncmp(e->d_name,"event",5)!=0)continue; std::string n="/sys/class/input/"+std::string(e->d_name)+"/device/name"; int f=open(n.c_str(),O_RDONLY); if(f<0)continue; char b[128]={}; ssize_t z=read(f,b,sizeof(b)-1); close(f); if(z>0 && std::string(b,z).find("Mouse")!=std::string::npos){*out="/dev/input/"+std::string(e->d_name);closedir(d);return true;} } closedir(d); return false;
#endif
}
bool PhysicalMouseReader::start(const std::string& requested,std::string* error){
#if defined(_WIN32)
 (void)requested; if(error)*error="Windows 不支持 evdev"; return false;
#else
 if(running_.exchange(true))return false; device_=requested; if(device_.empty()&&!find_device(&device_)){running_=false;if(error)*error="找不到物理鼠标 event 节点";return false;} fd_=open(device_.c_str(),O_RDONLY|O_NONBLOCK); if(fd_<0){running_=false;if(error)*error="无法打开物理鼠标: "+device_;return false;} thread_=std::thread(&PhysicalMouseReader::loop,this); return true;
#endif
}
void PhysicalMouseReader::stop(){
#if !defined(_WIN32)
 if(!running_.exchange(false))return; if(thread_.joinable())thread_.join(); if(fd_>=0){close(fd_);fd_=-1;}
#endif
}
void PhysicalMouseReader::loop(){
#if !defined(_WIN32)
 input_event ev{}; while(running_.load()){ if(read(fd_,&ev,sizeof(ev))!=(ssize_t)sizeof(ev)){usleep(1000);continue;} if(ev.type==EV_REL){if(ev.code==REL_X)rel_x_.fetch_add(ev.value);if(ev.code==REL_Y)rel_y_.fetch_add(ev.value);} if(ev.type==EV_KEY){uint16_t bit=0;if(ev.code==BTN_LEFT)bit=1;if(ev.code==BTN_RIGHT)bit=2;if(ev.code==BTN_MIDDLE)bit=4;if(ev.code==BTN_SIDE)bit=8;if(ev.code==BTN_EXTRA)bit=16;if(bit){if(ev.value)buttons_.fetch_or(bit);else buttons_.fetch_and((uint16_t)~bit);}} }
#endif
}
}
