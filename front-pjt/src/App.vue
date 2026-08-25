<script setup>
import { RouterView, useRoute } from 'vue-router';
import NavBar from '@/components/NavBar.vue';

const route = useRoute()
</script>

<template>
    <div id="app">
        <NavBar v-if="!route.meta.hideNavBar" />

        <main>
            <RouterView />
        </main>

    </div>
</template>

<style scoped></style>