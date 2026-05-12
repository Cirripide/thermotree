ARG NODE_VER
FROM node:${NODE_VER}-alpine

WORKDIR /app

RUN npm install -g @angular/cli

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 4200

CMD ["npm", "start", "--", "--host", "0.0.0.0"]
