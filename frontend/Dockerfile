# ---- 构建阶段：编译 Vue3 SPA ----
FROM node:22-alpine AS build

WORKDIR /src

# package-lock.json 存在，用 npm ci 保证可复现安装；先拷清单以复用依赖层缓存
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


# ---- 运行阶段：nginx 托管静态产物 + 反代后端 ----
FROM nginx:1.27-alpine

COPY --from=build /src/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
